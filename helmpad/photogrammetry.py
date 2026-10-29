"""Kopfmodell aus einem Video: Einzelbilder (ffmpeg) -> Kamerapositionen (COLMAP) -> Oberfläche (OpenMVS).

Alles läuft lokal auf der CPU, eine NVIDIA-Grafikkarte ist nicht nötig. Die Programme liegen in
%LOCALAPPDATA%\\helmpad-tools (colmap\\, openmvs\\) oder werden über die Umgebungsvariablen
HELMPAD_COLMAP / HELMPAD_OPENMVS gefunden; ffmpeg muss im PATH sein.

Das Ergebnis enthält noch Hintergrund (Zimmer, Boden). Es wird grob auf den Bereich zugeschnitten, auf den
alle Kameras zielen (der Kopf). Den Rest erledigt headscan.py nach dem Markieren.
"""
import json
import os
import shutil
import subprocess
import threading
from pathlib import Path

import numpy as np
import trimesh

TARGET_FRAMES = 90     # genug Überlappung für einen Rundumflug, Rechenzeit bleibt im Rahmen
MAX_FRAMES_EXHAUSTIVE = 150
IMAGE_SIZE = 1600      # längste Bildkante; mehr bringt am Kopf kaum Details, kostet aber viel Zeit

# Anteil der Gesamtzeit je Schritt (grobe Erfahrungswerte für den Fortschrittsbalken)
STAGES = [("frames", 0.03), ("features", 0.10), ("matching", 0.17), ("mapping", 0.20), ("undistort", 0.03),
          ("densify", 0.32), ("mesh", 0.12), ("cleanup", 0.03)]


class PhotoError(RuntimeError):
    def __init__(self, code, text, **args):
        super().__init__(text)
        self.code, self.args_ = code, args


class Cancelled(RuntimeError):
    pass


def _tool_dir():
    return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "helmpad-tools"


def tools():
    """Pfade zu den Programmen (None, wenn eines fehlt)."""
    base = _tool_dir()
    colmap = Path(os.environ.get("HELMPAD_COLMAP", base / "colmap" / "bin" / "colmap.exe"))
    mvs = Path(os.environ.get("HELMPAD_OPENMVS", base / "openmvs" / "vc17" / "x64" / "Release"))
    if not (mvs / "DensifyPointCloud.exe").exists() and (base / "openmvs").exists():
        hit = next((base / "openmvs").rglob("DensifyPointCloud.exe"), None)  # andere Ordnerstruktur im Archiv
        if hit:
            mvs = hit.parent
    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    found = {"colmap": colmap if colmap.exists() else None,
             "interface": mvs / "InterfaceCOLMAP.exe" if (mvs / "InterfaceCOLMAP.exe").exists() else None,
             "densify": mvs / "DensifyPointCloud.exe" if (mvs / "DensifyPointCloud.exe").exists() else None,
             "mesh": mvs / "ReconstructMesh.exe" if (mvs / "ReconstructMesh.exe").exists() else None,
             "ffmpeg": ffmpeg, "ffprobe": ffprobe}
    return found


def missing_tools():
    return [k for k, v in tools().items() if v is None]


class Job:
    """Eine laufende Rekonstruktion. Status ist threadsicher abfragbar."""

    def __init__(self, video, work):
        self.video, self.work = Path(video).resolve(), Path(work).resolve()
        self.stage, self.progress, self.error, self.done = "queued", 0.0, None, False
        self.result = None
        self._cancel = threading.Event()
        self._proc = None
        self._lock = threading.Lock()

    def status(self):
        with self._lock:
            return {"schritt": self.stage, "fortschritt": round(self.progress, 3), "fertig": self.done,
                    "fehler": self.error}

    def cancel(self):
        self._cancel.set()
        with self._lock:
            if self._proc and self._proc.poll() is None:
                self._proc.kill()

    # ---- interne Helfer
    def _set(self, stage, within=0.0):
        done = 0.0
        for name, w in STAGES:
            if name == stage:
                with self._lock:
                    self.stage, self.progress = stage, done + w * min(1.0, within)
                return
            done += w

    def _run(self, args, stage, log_name, cwd=None, expect_lines=None, watch=None):
        """Programm starten; Fortschritt aus der Zahl der Ausgabezeilen oder aus `watch()` (0..1)."""
        if self._cancel.is_set():
            raise Cancelled()
        self._set(stage, 0.0)
        log = (self.work / f"{log_name}.log").open("w", encoding="utf-8", errors="replace")
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        with self._lock:
            self._proc = subprocess.Popen([str(a) for a in args], cwd=cwd or self.work, stdout=subprocess.PIPE,
                                          stderr=subprocess.STDOUT, text=True, errors="replace", creationflags=flags)
        lines = 0
        stop = threading.Event()
        if watch:
            def poll():
                while not stop.wait(2.0):
                    self._set(stage, watch())
            threading.Thread(target=poll, daemon=True).start()
        for line in self._proc.stdout:
            log.write(line)
            lines += 1
            if expect_lines:
                self._set(stage, lines / expect_lines)
        code = self._proc.wait()
        stop.set()
        log.close()
        if self._cancel.is_set():
            raise Cancelled()
        if code != 0:
            raise PhotoError("photo_tool", f"Schritt „{stage}“ ist fehlgeschlagen (Code {code}). "
                             f"Details in {log_name}.log.", stage=stage)

    # ---- Ablauf
    def run(self):
        try:
            self.result = self._pipeline()
            with self._lock:
                self.stage, self.progress, self.done = "done", 1.0, True
        except Cancelled:
            with self._lock:
                self.stage, self.done, self.error = "cancelled", True, {"code": "photo_cancelled", "args": {},
                                                                       "text": "Abgebrochen."}
        except PhotoError as e:
            with self._lock:
                self.done, self.error = True, {"code": e.code, "args": e.args_, "text": str(e)}
        except Exception as e:  # unerwartet: trotzdem verständlich melden
            with self._lock:
                self.done = True
                self.error = {"code": "photo_internal", "args": {"detail": f"{type(e).__name__}: {e}"},
                              "text": f"Rekonstruktion fehlgeschlagen: {type(e).__name__}: {e}"}

    def _pipeline(self):
        t = tools()
        missing = [k for k, v in t.items() if v is None]
        if missing:
            raise PhotoError("photo_tools", "Photogrammetrie-Programme fehlen: " + ", ".join(missing),
                             tools=", ".join(missing))
        w = self.work
        images, sparse, dense = w / "bilder", w / "sparse", w / "dense"
        for d in (images, sparse):
            d.mkdir(parents=True, exist_ok=True)

        # 1) Einzelbilder
        self._set("frames")
        duration = self._duration(t["ffprobe"])
        fps = max(0.5, min(6.0, TARGET_FRAMES / duration)) if duration else 3.0
        scale = f"scale='if(gt(iw,ih),min({IMAGE_SIZE},iw),-2)':'if(gt(iw,ih),-2,min({IMAGE_SIZE},ih))'"
        self._run([t["ffmpeg"], "-hide_banner", "-loglevel", "error", "-i", self.video,
                   "-vf", f"fps={fps:.3f},{scale}", "-q:v", "2", images / "bild_%04d.jpg"], "frames", "1_ffmpeg")
        n = len(list(images.glob("*.jpg")))
        if n < 15:
            raise PhotoError("photo_frames", f"Aus dem Video kamen nur {n} Bilder – bitte mindestens 15 Sekunden "
                             "langsam rund um den Kopf filmen.", n=n)

        # 2) Merkmale, 3) Zuordnung, 4) Kamerapositionen (COLMAP, CPU)
        db = w / "datenbank.db"
        self._run([t["colmap"], "feature_extractor", "--database_path", db, "--image_path", images,
                   "--ImageReader.single_camera", "1", "--ImageReader.camera_model", "SIMPLE_RADIAL",
                   "--FeatureExtraction.use_gpu", "0", "--FeatureExtraction.max_image_size", IMAGE_SIZE],
                  "features", "2_merkmale", expect_lines=n * 3)
        matcher = "exhaustive_matcher" if n <= MAX_FRAMES_EXHAUSTIVE else "sequential_matcher"
        self._run([t["colmap"], matcher, "--database_path", db, "--FeatureMatching.use_gpu", "0"],
                  "matching", "3_zuordnung", expect_lines=max(n, 10))
        self._run([t["colmap"], "mapper", "--database_path", db, "--image_path", images, "--output_path", sparse,
                   "--Mapper.multiple_models", "0"], "mapping", "4_kameras", expect_lines=n * 25)
        model = self._best_model(sparse)
        if model is None:
            raise PhotoError("photo_no_model", "Aus dem Video ließ sich keine 3D-Szene berechnen – mehr Licht, "
                             "langsamer filmen und einen strukturierten Hintergrund verwenden.")
        used = self._registered(t["colmap"], model)
        if used < 0.5 * n:
            raise PhotoError("photo_few_images", f"Nur {used} von {n} Bildern passten zusammen – bitte gleichmäßiger "
                             "und langsamer rund um den Kopf filmen.", used=used, n=n)

        # 5) Entzerren, 6) dichte Punktwolke, 7) Oberfläche (OpenMVS)
        self._run([t["colmap"], "image_undistorter", "--image_path", images, "--input_path", model,
                   "--output_path", dense, "--output_type", "COLMAP", "--max_image_size", IMAGE_SIZE],
                  "undistort", "5_entzerren")
        self._run([t["interface"], "-i", dense, "-o", w / "szene.mvs", "--image-folder", dense / "images"],
                  "undistort", "5b_openmvs_import")
        # OpenMVS schreibt kaum auf die Konsole: Fortschritt an den fertigen Tiefenkarten ablesen (zwei Durchgänge)
        depth = lambda: (len(list(w.glob("depth*.dmap"))) / max(1, 2 * used))  # noqa: E731
        self._run([t["densify"], w / "szene.mvs", "-o", w / "szene_dicht.mvs", "--resolution-level", "1",
                   "--process-priority", "-1"], "densify", "6_punktwolke", watch=depth)
        self._run([t["mesh"], w / "szene_dicht.mvs", "-o", w / "szene_mesh.ply"],
                  "mesh", "7_oberflaeche", expect_lines=60)
        ply = w / "szene_mesh.ply"
        if not ply.exists():
            raise PhotoError("photo_no_mesh", "OpenMVS hat keine Oberfläche erzeugt.")

        # 8) auf den Kopf zuschneiden
        self._set("cleanup")
        mesh = trimesh.load(ply, force="mesh", process=False)
        mesh = crop_to_target(mesh, *self._cameras(t["colmap"], model))
        out = w / "kopf_scan.ply"
        mesh.export(out)
        for f in w.glob("depth*.dmap"):  # Tiefenkarten: viele hundert MB, werden nicht mehr gebraucht
            f.unlink(missing_ok=True)
        (w / "rekonstruktion.json").write_text(json.dumps({"bilder": n, "verwendet": used,
                                                           "dreiecke": int(len(mesh.faces))}), encoding="utf-8")
        return out

    def _duration(self, ffprobe):
        try:
            out = subprocess.run([ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                                  str(self.video)], capture_output=True, text=True, timeout=60,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            return float(out.stdout.strip())
        except (ValueError, subprocess.SubprocessError):
            return None

    @staticmethod
    def _best_model(sparse):
        models = [p for p in sparse.iterdir() if p.is_dir() and (p / "images.bin").exists()]
        return max(models, key=lambda p: (p / "images.bin").stat().st_size) if models else None

    def _text_model(self, colmap, model):
        txt = self.work / "modell_txt"
        if not (txt / "images.txt").exists():
            txt.mkdir(exist_ok=True)
            self._run([colmap, "model_converter", "--input_path", model, "--output_path", txt,
                       "--output_type", "TXT"], "mapping", "4b_modell_text")
        return txt

    def _registered(self, colmap, model):
        return len(_read_cameras(self._text_model(colmap, model) / "images.txt")[0])

    def _cameras(self, colmap, model):
        return _read_cameras(self._text_model(colmap, model) / "images.txt")


def _read_cameras(images_txt):
    """Kamerazentren und Blickrichtungen aus COLMAPs images.txt."""
    centers, views = [], []
    lines = [l for l in images_txt.read_text(encoding="utf-8", errors="replace").splitlines() if not l.startswith("#")]
    for line in lines[0::2]:  # jede zweite Zeile ist die Liste der 2D-Punkte
        parts = line.split()
        if len(parts) < 10:
            continue
        qw, qx, qy, qz, tx, ty, tz = map(float, parts[1:8])
        R = np.array([
            [1 - 2 * (qy * qy + qz * qz), 2 * (qx * qy - qz * qw), 2 * (qx * qz + qy * qw)],
            [2 * (qx * qy + qz * qw), 1 - 2 * (qx * qx + qz * qz), 2 * (qy * qz - qx * qw)],
            [2 * (qx * qz - qy * qw), 2 * (qy * qz + qx * qw), 1 - 2 * (qx * qx + qy * qy)]])
        centers.append(-R.T @ np.array([tx, ty, tz]))
        views.append(R.T @ np.array([0.0, 0.0, 1.0]))
    return np.array(centers), np.array(views)


def crop_to_target(mesh, centers, views, keep=0.75):
    """Nur den Bereich behalten, auf den die Kameras gemeinsam zielen (Kugel um den Zielpunkt)."""
    if len(centers) < 3:
        return mesh
    # Punkt mit kleinstem Abstand zu allen Sichtstrahlen (kleinste Quadrate)
    A, b = np.zeros((3, 3)), np.zeros(3)
    for c, v in zip(centers, views):
        P = np.eye(3) - np.outer(v, v)
        A += P
        b += P @ c
    target = np.linalg.lstsq(A, b, rcond=None)[0]
    radius = keep * float(np.median(np.linalg.norm(centers - target, axis=1)))
    v = np.asarray(mesh.vertices)
    inside = np.linalg.norm(v - target, axis=1) < radius
    faces = mesh.faces[inside[mesh.faces].all(axis=1)]
    if len(faces) < 1000:  # Zuschnitt ging schief – lieber alles behalten
        return mesh
    cropped = trimesh.Trimesh(v, faces, process=False)
    cropped.remove_unreferenced_vertices()
    # Kopf und Körper hängen zusammen; abgetrennte Krümel weg
    parts = cropped.split(only_watertight=False, repair=False)
    if len(parts) > 1:
        sizes = np.array([len(p.faces) for p in parts])
        cropped = trimesh.util.concatenate([p for p, s in zip(parts, sizes) if s >= 0.02 * sizes.max()])
    return cropped


# ---- Einrichtung der Programme (einmalig, ~150 MB Download von den offiziellen GitHub-Seiten)
DOWNLOADS = {
    "colmap": "https://github.com/colmap/colmap/releases/download/4.2.1/colmap-x64-windows-nocuda.zip",
    "openmvs": "https://github.com/cdcseacave/openMVS/releases/download/v2.4.0/OpenMVS_Windows_x64.zip",
}


def install(log=print):
    """COLMAP (ohne CUDA) und OpenMVS nach %LOCALAPPDATA%\\helmpad-tools laden und entpacken."""
    import urllib.request
    import zipfile

    base = _tool_dir()
    base.mkdir(parents=True, exist_ok=True)
    for name, url in DOWNLOADS.items():
        target = base / name
        if name == "colmap" and (target / "bin" / "colmap.exe").exists() or \
                name == "openmvs" and any(target.rglob("DensifyPointCloud.exe")):
            log(f"{name}: schon vorhanden")
            continue
        archive = base / f"{name}.zip"
        log(f"{name}: lade {url} ...")
        urllib.request.urlretrieve(url, archive)
        log(f"{name}: entpacke ...")
        with zipfile.ZipFile(archive) as z:
            z.extractall(target)
        archive.unlink()
    if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        log("ffmpeg fehlt noch – in einer Eingabeaufforderung: winget install Gyan.FFmpeg")
    missing = missing_tools()
    log("Fertig." if not missing else "Es fehlt noch: " + ", ".join(missing))
    return not missing


if __name__ == "__main__":
    install()
