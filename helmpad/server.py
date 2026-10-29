"""Lokale Web-Oberfläche: python -m helmpad.server  (öffnet http://127.0.0.1:8770)"""
import argparse
import base64
import io
import re
import shutil
import threading
import time
import uuid
import webbrowser
import zipfile
from dataclasses import asdict
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

import numpy as np
import trimesh
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from . import photogrammetry as photo
from .head import HeadMeasurements, build_head
from .headscan import ScanError, head_from_scan, load_scan
from .helmet import AXES
from .messages import msg
from .orient import detect_orientation
from .pads import PadParams
from .pipeline import run
from .rays import dirs_from_az_el
from .accessories import MagnetParams
from .split import Cut, DowelParams
from .zones import PRESETS, Zone, safe_names

WEB = Path(__file__).parent / "web"
DATA = Path(__file__).resolve().parent.parent / "arbeitsdaten"
UPLOADS, JOBS, SCANS = DATA / "helme", DATA / "ergebnisse", DATA / "koepfe"
ALLOWED_EXT = {".stl", ".obj", ".ply", ".3mf", ".off", ".glb"}
MAX_UPLOAD_MB = 300
SCAN_EXT = {".stl", ".obj", ".ply", ".glb", ".off", ".3mf"}
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm"}
MAX_VIDEO_MB = 2000
SCAN_WORK_FACES = 400_000  # Kopfberechnung auf vereinfachtem Scan: schnell genug, Details reichen locker
PREVIEW_FACES = 150_000
ID_RE = re.compile(r"^[0-9a-f]{32}$")
LANDMARKS = {"augen", "scheitel", "kinn", "ohr_links", "ohr_rechts"}
LOCAL_HOSTS = {"127.0.0.1", "localhost", "[::1]"}

# Aufräumen: Ergebnisse älter als 12 h (die neuesten 10 bleiben immer), Uploads älter als 14 Tage
KEEP_RESULTS, RESULT_MAX_AGE_H, UPLOAD_MAX_AGE_D = 10, 12, 14

app = FastAPI(title="HelmPad")
# Eine Berechnung braucht alle CPU-Kerne – weitere Anfragen warten, statt den Rechner zu überlasten
COMPUTE_LOCK = threading.Lock()
# Video-Rekonstruktionen (id -> photogrammetry.Job); immer nur eine gleichzeitig
PHOTO_JOBS: dict = {}


def fail(status, code, text, **args):
    """Fehler mit Code + Werten, damit die Oberfläche ihn in der gewählten Sprache anzeigen kann."""
    raise HTTPException(status, msg(code, text, **args))


@app.middleware("http")
async def local_only(request: Request, call_next):
    """Nur Anfragen von dieser App selbst zulassen.

    Ohne diese Prüfung könnte jede besuchte Webseite Dateien an den lokalen Server schicken
    (Host-Prüfung gegen DNS-Rebinding, Origin-Prüfung gegen fremde Seiten).
    """
    host = (request.headers.get("host") or "").rsplit(":", 1)[0].lower()
    origin = request.headers.get("origin")
    if host not in LOCAL_HOSTS or (origin and urlsplit(origin).hostname not in {"127.0.0.1", "localhost", "::1"}):
        return JSONResponse({"detail": msg("forbidden", "Zugriff nur von dieser App aus erlaubt.")}, status_code=403)
    return await call_next(request)


@app.exception_handler(RequestValidationError)
async def invalid_input(request: Request, exc: RequestValidationError):
    err = exc.errors()[0] if exc.errors() else {}
    field = ".".join(str(x) for x in err.get("loc", [])[1:]) or "?"
    return JSONResponse({"detail": msg("invalid_input", f"Ungültige Eingabe: {field}", field=field)}, status_code=422)


def _check_id(i):
    if not ID_RE.match(i):
        fail(404, "not_found", "Unbekannte ID")
    return i


def _guess_units(max_extent):
    if max_extent < 1.0:
        return "m"
    if max_extent < 15:
        return "in"
    if max_extent < 60:
        return "cm"
    return "mm"


def cleanup():
    """Alte Arbeitsdaten löschen, damit der Ordner nicht unbegrenzt wächst."""
    now = time.time()
    if JOBS.exists():
        jobs = sorted((p for p in JOBS.iterdir() if p.is_dir()), key=lambda p: p.stat().st_mtime, reverse=True)
        for p in jobs[KEEP_RESULTS:]:
            if now - p.stat().st_mtime > RESULT_MAX_AGE_H * 3600:
                shutil.rmtree(p, ignore_errors=True)
    for root in (UPLOADS, SCANS):
        if not root.exists():
            continue
        for p in root.iterdir():
            job = PHOTO_JOBS.get(p.name)
            if job and not job.done:
                continue
            if p.is_dir() and now - p.stat().st_mtime > UPLOAD_MAX_AGE_D * 86400:
                shutil.rmtree(p, ignore_errors=True)
                PHOTO_JOBS.pop(p.name, None)


@app.post("/api/helm")
def upload_helmet(datei: UploadFile = File(...)):
    # bewusst ohne async: Laden und Vereinfachen großer Meshes läuft im Thread-Pool und blockiert
    # so nicht die übrigen Anfragen
    ext = Path(datei.filename or "").suffix.lower()
    if ext not in ALLOWED_EXT:
        allowed = ", ".join(sorted(ALLOWED_EXT))
        fail(400, "file_type", f"Dateityp {ext or '?'} wird nicht unterstützt (erlaubt: {allowed})", ext=ext or "?", allowed=allowed)
    cleanup()
    hid = uuid.uuid4().hex
    folder = UPLOADS / hid
    folder.mkdir(parents=True)
    path = folder / f"helm{ext}"
    try:
        size, limit = 0, MAX_UPLOAD_MB * 1024 * 1024
        with path.open("wb") as f:
            while chunk := datei.file.read(1024 * 1024):
                size += len(chunk)
                if size > limit:
                    raise OverflowError
                f.write(chunk)
        mesh = trimesh.load(path, force="mesh")
        if not isinstance(mesh, trimesh.Trimesh) or len(mesh.faces) == 0:
            raise ValueError("keine Dreiecke gefunden")
        faces = len(mesh.faces)
        view = mesh.simplify_quadric_decimation(face_count=PREVIEW_FACES) if faces > PREVIEW_FACES else mesh
        view.export(folder / "vorschau.stl")
        try:
            orientation = detect_orientation(view)
        except Exception:  # nur ein Vorschlag – ohne ihn geht es auch
            orientation = None
    except OverflowError:
        shutil.rmtree(folder, ignore_errors=True)
        fail(413, "file_too_big", f"Die Datei ist größer als {MAX_UPLOAD_MB} MB.", mb=MAX_UPLOAD_MB)
    except Exception as e:
        shutil.rmtree(folder, ignore_errors=True)
        fail(400, "file_read", f"Datei konnte nicht gelesen werden: {e}", detail=str(e))
    return {
        "id": hid,
        "name": datei.filename,
        "dreiecke": faces,
        "masse": [round(float(v), 2) for v in mesh.extents],
        "wasserdicht": bool(mesh.is_watertight),
        "einheit_vorschlag": _guess_units(float(mesh.extents.max())),
        "ausrichtung": orientation,
    }


def _save_upload(datei: UploadFile, path: Path, max_mb: int):
    size, limit = 0, max_mb * 1024 * 1024
    with path.open("wb") as f:
        while chunk := datei.file.read(1024 * 1024):
            size += len(chunk)
            if size > limit:
                raise OverflowError
            f.write(chunk)


def _prepare_scan(folder: Path, mesh):
    """Vereinfachte Arbeitskopie und Vorschau eines Kopfscans ablegen."""
    work = mesh.simplify_quadric_decimation(face_count=SCAN_WORK_FACES) if len(mesh.faces) > SCAN_WORK_FACES else mesh
    work.export(folder / "arbeit.ply")
    view = work.simplify_quadric_decimation(face_count=PREVIEW_FACES) if len(work.faces) > PREVIEW_FACES else work
    view.export(folder / "vorschau.stl")
    (folder / "dreiecke.txt").write_text(str(len(mesh.faces)))
    return len(mesh.faces)


@app.post("/api/kopfscan")
def upload_head_scan(datei: UploadFile = File(...)):
    ext = Path(datei.filename or "").suffix.lower()
    if ext not in SCAN_EXT:
        allowed = ", ".join(sorted(SCAN_EXT))
        fail(400, "file_type", f"Dateityp {ext or '?'} wird nicht unterstützt (erlaubt: {allowed})", ext=ext or "?", allowed=allowed)
    cleanup()
    sid = uuid.uuid4().hex
    folder = SCANS / sid
    folder.mkdir(parents=True)
    path = folder / f"scan{ext}"
    try:
        _save_upload(datei, path, MAX_UPLOAD_MB)
        faces = _prepare_scan(folder, load_scan(path))
    except OverflowError:
        shutil.rmtree(folder, ignore_errors=True)
        fail(413, "file_too_big", f"Die Datei ist größer als {MAX_UPLOAD_MB} MB.", mb=MAX_UPLOAD_MB)
    except ScanError as e:
        shutil.rmtree(folder, ignore_errors=True)
        fail(400, e.code, str(e), **e.args_)
    except Exception as e:
        shutil.rmtree(folder, ignore_errors=True)
        fail(400, "file_read", f"Datei konnte nicht gelesen werden: {e}", detail=str(e))
    return {"id": sid, "name": datei.filename, "dreiecke": faces}


def _photo_worker(sid: str, job):
    job.run()
    if job.result is not None:
        try:
            _prepare_scan(SCANS / sid, load_scan(job.result))
            # Einzelbilder und dichte Daten sind groß und werden nicht mehr gebraucht
            for d in ("bilder", "dense"):
                shutil.rmtree(job.work / d, ignore_errors=True)
        except Exception as e:
            job.error = {"code": "photo_internal", "args": {"detail": str(e)}, "text": f"Kopfmodell unbrauchbar: {e}"}


@app.get("/api/kopfvideo/bereit")
def photo_ready():
    missing = photo.missing_tools()
    return {"bereit": not missing, "fehlt": missing}


@app.post("/api/kopfvideo")
def upload_head_video(datei: UploadFile = File(...)):
    ext = Path(datei.filename or "").suffix.lower()
    if ext not in VIDEO_EXT:
        allowed = ", ".join(sorted(VIDEO_EXT))
        fail(400, "file_type", f"Dateityp {ext or '?'} wird nicht unterstützt (erlaubt: {allowed})", ext=ext or "?", allowed=allowed)
    missing = photo.missing_tools()
    if missing:
        fail(400, "photo_tools", "Photogrammetrie-Programme fehlen: " + ", ".join(missing), tools=", ".join(missing))
    if any(not j.done for j in PHOTO_JOBS.values()):
        fail(409, "photo_busy", "Es läuft bereits eine Video-Auswertung – bitte warten oder abbrechen.")
    cleanup()
    sid = uuid.uuid4().hex
    folder = SCANS / sid
    folder.mkdir(parents=True)
    path = folder / f"video{ext}"
    try:
        _save_upload(datei, path, MAX_VIDEO_MB)
    except OverflowError:
        shutil.rmtree(folder, ignore_errors=True)
        fail(413, "file_too_big", f"Die Datei ist größer als {MAX_VIDEO_MB} MB.", mb=MAX_VIDEO_MB)
    (folder / "arbeit_video").mkdir()
    job = photo.Job(path, folder / "arbeit_video")
    PHOTO_JOBS[sid] = job
    threading.Thread(target=_photo_worker, args=(sid, job), daemon=True).start()
    return {"id": sid, "name": datei.filename}


@app.get("/api/kopfscan/{sid}/status")
def head_scan_status(sid: str):
    folder = SCANS / _check_id(sid)
    job = PHOTO_JOBS.get(sid)
    ready = (folder / "vorschau.stl").exists()
    if job is None:
        if not ready:
            fail(404, "scan_missing", "Kopfmodell nicht gefunden")
        faces = folder / "dreiecke.txt"
        return {"schritt": "done", "fortschritt": 1.0, "fertig": True, "fehler": None, "bereit": True,
                "dreiecke": int(faces.read_text()) if faces.exists() else None}
    st = job.status()
    if job.error and not st["fehler"]:
        st["fehler"] = job.error
    st["bereit"] = ready and not st["fehler"]
    st["fertig"] = st["fertig"] and (ready or bool(st["fehler"]))
    if (folder / "dreiecke.txt").exists():
        st["dreiecke"] = int((folder / "dreiecke.txt").read_text())
    return st


@app.post("/api/kopfscan/{sid}/abbrechen")
def head_scan_cancel(sid: str):
    job = PHOTO_JOBS.get(_check_id(sid))
    if job:
        job.cancel()
    return {"ok": True}


@app.get("/api/kopfscan/{sid}/vorschau.stl")
def head_scan_preview(sid: str):
    path = SCANS / _check_id(sid) / "vorschau.stl"
    if not path.exists():
        fail(404, "scan_missing", "Kopfmodell nicht gefunden")
    return FileResponse(path, media_type="model/stl")


def _scan_head(sid: str, marks: dict, circumference: float, subdivisions: int):
    path = SCANS / _check_id(sid) / "arbeit.ply"
    if not path.exists():
        fail(404, "scan_missing", "Kopfmodell nicht gefunden – bitte neu laden")
    try:
        return head_from_scan(load_scan(path), marks, circumference, subdivisions=subdivisions)
    except ScanError as e:
        fail(400, e.code, str(e), **e.args_)


@app.get("/api/helm/{hid}/vorschau.stl")
def helmet_preview(hid: str):
    path = UPLOADS / _check_id(hid) / "vorschau.stl"
    if not path.exists():
        fail(404, "helm_missing", "Helm nicht gefunden")
    return FileResponse(path, media_type="model/stl")


class Strict(BaseModel):
    # NaN/Unendlich ablehnen: sie liefen sonst bis zum Ende durch und scheiterten erst beim Antworten
    model_config = ConfigDict(allow_inf_nan=False)


Mm = Field(default=None, gt=0, le=1000)
Vec3 = tuple[float, float, float]


class Measurements(Strict):
    umfang: float = Field(gt=0, le=2000)
    laenge: float | None = Mm
    breite: float | None = Mm
    ohr_scheitel: float | None = Mm
    gesicht: float | None = Mm

    def to_head(self):
        if not 400 <= self.umfang <= 750:
            fail(400, "umfang_range", "Kopfumfang bitte in mm angeben (üblich: 520–640)")
        return HeadMeasurements(self.umfang, self.laenge or None, self.breite or None,
                                self.ohr_scheitel or None, self.gesicht or None)


@app.post("/api/kopf")
def head_preview(m: Measurements):
    try:
        mesh, shape, info = build_head(m.to_head(), subdivisions=4)
    except ValueError as e:
        fail(400, "shape_mismatch", f"Maße passen nicht zusammen: {e}", detail=str(e))
    # Radius-Tabelle, damit die Oberfläche Zonen direkt auf den Kopf zeichnen kann
    step = 2.0
    az = np.arange(-180, 180 + 1e-9, step)
    el = np.arange(-90, 90 + 1e-9, step)
    AZ, EL = np.meshgrid(az, el)
    r = shape.radius(dirs_from_az_el(AZ, EL))
    return {
        "info": info,
        "stl": base64.b64encode(mesh.export(file_type="stl")).decode(),
        "radial": {"step": step, "n_az": len(az), "n_el": len(el), "r": np.round(r, 2).ravel().tolist()},
    }


class ScanRef(Strict):
    id: str
    markierungen: dict[str, Vec3]


class ScanHeadRequest(ScanRef):
    umfang: float = Field(gt=0, le=2000)


@app.post("/api/kopfscan/kopf")
def head_scan_head(req: ScanHeadRequest):
    if not 400 <= req.umfang <= 750:
        fail(400, "umfang_range", "Kopfumfang bitte in mm angeben (üblich: 520–640)")
    mesh, info, radial = _scan_head(req.id, req.markierungen, req.umfang, subdivisions=4)
    return {"info": info, "stl": base64.b64encode(mesh.export(file_type="stl")).decode(), "radial": radial}


@app.get("/api/vorlagen")
def presets():
    return {k: {"titel": t, "zonen": [asdict(z) for z in zs]} for k, (t, zs) in PRESETS.items()}


class ZoneIn(Strict):
    name: str = Field(max_length=200)
    az: float = Field(ge=-360, le=360)
    el: float = Field(ge=-90, le=90)
    half_w: float = Field(gt=0, le=90)   # Grad; größer ergibt riesige Raster und Rechenzeiten
    half_h: float = Field(gt=0, le=90)
    roundness: float = Field(default=0.6, ge=0, le=1)
    roll: float = Field(default=0.0, ge=-360, le=360)


class CutIn(Strict):
    achse: Literal["x", "y", "z"]
    pos: float = Field(default=0.0, ge=-500, le=500)


Axis = Literal[tuple(AXES)]


class ComputeRequest(Strict):
    helm_id: str
    einheit: Literal["mm", "cm", "m", "in"] = "mm"
    oben: Axis = "+z"
    vorne: Axis = "-y"
    masse: Measurements
    zonen: list[ZoneIn] = Field(max_length=30)
    dicke: float = Field(default=10.0, ge=2, le=40)
    kompression: float = Field(default=2.0, ge=0, le=10)
    klett: float = Field(default=3.0, ge=0, le=15)
    kanaele: float = Field(default=22.0, ge=0, le=100)
    skalierung: float | None = Field(default=None, ge=0.2, le=5)
    versatz: Vec3 = (0.0, 0.0, 0.0)
    rotation: tuple[Vec3, Vec3, Vec3] | None = None   # 3x3, Datei -> Kopf-Koordinaten
    markierungen: dict[str, Vec3] | None = None       # Punkte in Datei-Koordinaten
    proportional: bool = False
    max_verzerrung: float = Field(default=12.0, ge=0, le=30)  # Prozent
    einbau_markierung: Literal["rille", "steg", "keine"] = "rille"
    polster_nummern: bool = True
    teilung: Literal["keine", "auto", "manuell"] = "keine"
    schnitte: list[CutIn] = Field(default=[], max_length=6)
    bauraum: Vec3 = (256.0, 256.0, 256.0)
    duebel_d: float = Field(default=5.0, ge=2, le=15)
    duebel_l: float = Field(default=16.0, ge=6, le=60)
    duebel_abstand: float = Field(default=55.0, ge=15, le=500)
    duebel_spiel: float = Field(default=0.15, ge=0, le=1)
    probestueck: bool = True
    befestigung: Literal["klett", "magnet"] = "klett"
    magnet_d: float = Field(default=10.0, ge=3, le=25)
    magnet_h: float = Field(default=3.0, ge=1, le=10)
    brille: bool = False
    riemen: float | None = Field(default=None, ge=10, le=50)      # Gurtbreite in mm
    luefter: Literal[25, 30, 40] | None = None                     # Lüftergröße in mm
    mikrofon: float | None = Field(default=None, ge=3, le=15)     # Durchmesser Mikrofonarm
    muster: bool = False                                          # 2D-Schnittmuster für Schaumstoff
    muster_staerke: float = Field(default=6.0, ge=1, le=30)
    papier: Literal["A4", "Letter"] = "A4"
    kopfscan: ScanRef | None = None                               # gescannter Kopf statt Modell aus Maßen


@app.post("/api/berechnen")
def compute(req: ComputeRequest):
    folder = UPLOADS / _check_id(req.helm_id)
    helm = next(folder.glob("helm.*"), None)
    if helm is None:
        fail(404, "helm_missing", "Helm nicht gefunden – bitte neu hochladen")
    if not req.zonen:
        fail(400, "need_zone", "Mindestens eine Polsterzone wird benötigt")
    names = safe_names(z.name for z in req.zonen)
    zones = [Zone(**{**z.model_dump(), "name": n}) for z, n in zip(req.zonen, names)]
    head = req.masse.to_head()
    head_override = None
    if req.kopfscan:
        mesh, info, _ = _scan_head(req.kopfscan.id, req.kopfscan.markierungen, head.circumference, subdivisions=5)
        head_override = (mesh, info)

    pp = PadParams(thickness=req.dicke, compression=req.kompression, mount_gap=req.klett,
                   channel_spacing=0.0 if req.kanaele < 8 else req.kanaele)  # engere Kanäle sind nicht druckbar
    with COMPUTE_LOCK:
        cleanup()
        jid = uuid.uuid4().hex
        out = JOBS / jid
        try:
            report, _, _, pads = run(
                helm, head, zones, pp, out, up=req.oben, front=req.vorne, units=req.einheit,
                scale=req.skalierung, offset=tuple(max(-100.0, min(100.0, v)) for v in req.versatz),
                log=lambda *_: None, viewer=False, rotation=req.rotation,
                landmarks={k: v for k, v in (req.markierungen or {}).items() if k in LANDMARKS},
                proportional=req.proportional, max_distort=req.max_verzerrung / 100,
                mark_mode=req.einbau_markierung, pad_numbers=req.polster_nummern,
                split_mode=req.teilung, cuts=[Cut(c.achse, c.pos) for c in req.schnitte],
                bed=tuple(min(2000.0, max(50.0, v)) for v in req.bauraum),
                dowel=DowelParams(diameter=req.duebel_d, length=req.duebel_l,
                                  spacing=req.duebel_abstand, clearance=req.duebel_spiel),
                test_piece=req.probestueck, mount=req.befestigung,
                magnet=MagnetParams(diameter=req.magnet_d, height=req.magnet_h), glasses=req.brille,
                accessories={"riemen": req.riemen, "luefter": req.luefter, "mikrofon": req.mikrofon},
                pattern={"sheet": req.muster_staerke, "paper": req.papier} if req.muster else None,
                head_override=head_override)
            for p in pads:  # Vorschau in Kopf-Koordinaten (die Druckdateien sind gedreht)
                p.mesh.export(out / "polster" / f"{p.file_stem}.vorschau.stl")
        except HTTPException:
            shutil.rmtree(out, ignore_errors=True)
            raise
        except ValueError as e:
            shutil.rmtree(out, ignore_errors=True)
            fail(400, "value", str(e), detail=str(e))
        except Exception as e:  # nichts Halbfertiges liegen lassen, verständlicher Fehler statt 500
            shutil.rmtree(out, ignore_errors=True)
            fail(500, "internal", f"Berechnung fehlgeschlagen: {type(e).__name__}: {e}", detail=f"{type(e).__name__}: {e}")

    base = f"/api/ergebnis/{jid}"
    return {
        "id": jid,
        "bericht": report,
        "dateien": {
            "kopf": f"{base}/kopf.stl",
            "helm": f"{base}/helm_angepasst.stl",
            "original": f"{base}/helm_original.stl",
            "zip": f"{base}/zip",
            "polster": [{"name": p.zone.name, "nummer": i,
                         "mitte": [round(float(v), 1) for v in p.back_dir * (p.back_r - p.stats["dicke_mittel_mm"])],
                         "vorschau": f"{base}/polster/{p.file_stem}.vorschau.stl",
                         "druck": f"{base}/polster/{p.file_stem}.stl"} for i, p in enumerate(pads, 1)],
            "teile": [{**t, "druck": f"{base}/{t['datei']}",
                       "vorschau": f"{base}/{t['datei'].replace('.stl', '.vorschau.stl')}"}
                      for t in report["teilung"]["teile"]],
            "probestueck": f"{base}/{report['probestueck']['datei']}" if report["probestueck"] else None,
            "zubehoer": [{**e, "druck": f"{base}/{e['datei']}"} for e in report["zubehoer"]],
            "schnittmuster": {k: f"{base}/{v}" for k, v in report["schnittmuster"]["dateien"].items()}
            if report["schnittmuster"] else None,
            "platten": {k: [{**pl, "url": f"{base}/3mf/{pl['datei']}"} for pl in v] for k, v in report["platten"].items()},
            "duebel": None if not (report["teilung"]["duebel"] or {}).get("anzahl") else {
                "einzeln": f"{base}/helm_teile/duebel_einzeln.stl",
                "satz": f"{base}/{report['teilung']['duebel']['satz_datei']}",
                "vorschau": f"{base}/helm_teile/duebel.vorschau.stl"},
        },
    }


@app.get("/api/ergebnis/{jid}/zip")
def result_zip(jid: str):
    out = JOBS / _check_id(jid)
    if not (out / "bericht.json").exists():  # nur vollständige Ergebnisse
        fail(404, "not_found", "Ergebnis nicht gefunden")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for sub in ("polster", "helm_teile", "zubehoer", "3mf", "schnittmuster"):
            for f in sorted(f for f in (out / sub).glob("*") if f.suffix in (".stl", ".3mf", ".pdf", ".svg", ".dxf")) if (out / sub).exists() else []:
                if not f.name.endswith(".vorschau.stl"):
                    z.write(f, f"{sub}/{f.name}")
        for name in ("helm_angepasst.stl", "kopf.stl", "bericht.json", "zonen.json"):
            if (out / name).exists():
                z.write(out / name, name)
    return Response(buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": 'attachment; filename="helmpad_polster.zip"'})


@app.get("/api/ergebnis/{jid}/{rel:path}")
def result_file(jid: str, rel: str):
    out = (JOBS / _check_id(jid)).resolve()
    path = (out / rel).resolve()
    if out not in path.parents or not path.is_file():
        fail(404, "not_found", "Datei nicht gefunden")
    return FileResponse(path, filename=path.name)


app.mount("/", StaticFiles(directory=WEB, html=True), name="web")


def main():
    import uvicorn

    ap = argparse.ArgumentParser(description="HelmPad Oberfläche starten")
    ap.add_argument("--port", type=int, default=8770)
    ap.add_argument("--kein-browser", action="store_true")
    a = ap.parse_args()
    url = f"http://127.0.0.1:{a.port}"
    cleanup()
    print(f"HelmPad läuft auf {url}  (Beenden mit Strg+C)\nProgrammordner: {WEB.parent.parent}")
    if not a.kein_browser:
        webbrowser.open(url)
    uvicorn.run(app, host="127.0.0.1", port=a.port, log_level="warning")


if __name__ == "__main__":
    main()
