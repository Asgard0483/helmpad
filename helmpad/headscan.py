"""Kopfmodell aus einem 3D-Scan (Handy-App wie RealityScan/Polycam oder Photogrammetrie aus einem Video).

Der Scan kommt in beliebiger Lage, Größe und Einheit. Mit drei markierten Punkten (Ohr links, Ohr rechts,
Augen) wird er in das Kopf-Koordinatensystem gebracht (Ursprung zwischen den Ohrkanälen, X rechts, Y vorne,
Z oben). Die Größe stammt aus dem gemessenen Kopfumfang: Der Querschnitt auf Augenbrauenhöhe wird so skaliert,
dass sein Umfang (wie beim Maßband: die straff gespannte, konvexe Hülle) dem Messwert entspricht.

Danach wird der Scan als sternförmiger Körper neu abgetastet (Radius je Richtung). Das glättet Löcher und
Rauschen, entfernt Hintergrund und Schultern und liefert dieselbe Form wie das berechnete Kopfmodell –
alles Weitere (Anpassung, Polster, Schnittmuster) läuft dadurch unverändert.
"""
import numpy as np
import trimesh
from scipy.interpolate import RegularGridInterpolator
from scipy.ndimage import distance_transform_edt, median_filter
from scipy.spatial import ConvexHull

from .head import EYE_BELOW_BROW
from .messages import msg
from .rays import az_el_from_dirs, dirs_from_az_el

EYE_ABOVE_EAR = 10.0    # Pupillen liegen etwa so hoch über der Ohrkanal-Linie (wie im berechneten Kopf)
EAR_CANAL_DIST = 135.0  # typischer Abstand der Ohrkanäle, nur als Startwert für die Größe
MAX_RADIUS = 175.0      # weiter weg ist kein Kopf mehr (Hintergrund, Schultern)
CHIN_LIMIT = -150.0     # darunter beginnt Hals/Körper
REQUIRED = ("ohr_links", "ohr_rechts", "augen", "scheitel")


class ScanError(ValueError):
    def __init__(self, code, text, **args):
        super().__init__(text)
        self.code, self.args_ = code, args


def load_scan(path):
    mesh = trimesh.load(path, force="mesh", process=False)
    if not isinstance(mesh, trimesh.Trimesh) or len(mesh.faces) == 0:
        raise ScanError("scan_empty", "Im Kopfmodell wurden keine Dreiecke gefunden.")
    return mesh


def scan_frame(marks):
    """Ursprung und Drehung (Zeilen X/Y/Z) aus den markierten Punkten in Datei-Koordinaten."""
    missing = [k for k in REQUIRED if marks.get(k) is None]
    if missing:
        raise ScanError("scan_marks", "Bitte am Kopfmodell beide Ohren, die Augen und den Scheitel markieren.")
    left, right, eyes = (np.asarray(marks[k], float) for k in REQUIRED[:3])
    origin = (left + right) / 2
    x = right - left
    ear_dist = float(np.linalg.norm(x))
    if ear_dist < 1e-9:
        raise ScanError("scan_marks", "Die Ohrpunkte liegen aufeinander.")
    x /= ear_dist
    y = eyes - origin
    y -= (y @ x) * x
    if np.linalg.norm(y) < 1e-6 * ear_dist:
        raise ScanError("scan_marks", "Die Augen liegen auf der Linie zwischen den Ohren.")
    fwd = float(np.linalg.norm(y))
    y /= fwd
    z = np.cross(x, y)
    if z @ (np.asarray(marks["scheitel"], float) - origin) < 0:
        # Scheitel liegt unten -> Ohren vertauscht markiert (links/rechts aus Sicht des Kopfes ist leicht verwechselt)
        x, z = -x, -z
    # Die Augen liegen etwas über der Ohrlinie: um die Querachse kippen, damit „oben“ senkrecht steht
    # (Verhältnis ist maßstabsunabhängig, Startgröße aus dem Ohrabstand)
    tilt = np.arcsin(np.clip(EYE_ABOVE_EAR / (fwd * EAR_CANAL_DIST / ear_dist), -0.5, 0.5))
    y, z = np.cos(tilt) * y - np.sin(tilt) * z, np.sin(tilt) * y + np.cos(tilt) * z
    return origin, np.stack([x, y, z]), ear_dist


def _hull_perimeter(pts2d):
    if len(pts2d) < 3:
        return None
    hull = ConvexHull(pts2d)
    ring = pts2d[hull.vertices]
    return float(np.linalg.norm(np.roll(ring, -1, axis=0) - ring, axis=1).sum())


def slice_circumference(mesh, z, limit=MAX_RADIUS - 15):
    """Maßband-Umfang (konvexe Hülle) des Querschnitts auf Höhe z, nur bis `limit` um die Kopfachse."""
    sec = mesh.section(plane_origin=[0, 0, z], plane_normal=[0, 0, 1])
    if sec is None:
        return None
    pts = np.asarray(sec.vertices)[:, :2]
    pts = pts[np.linalg.norm(pts, axis=1) < limit]
    return _hull_perimeter(pts)


def _radial_grid(mesh, step):
    """Größter Radius je Richtung (äußerste Oberfläche, Ohrmuscheln inklusive), Lücken aufgefüllt."""
    az = np.arange(-180.0, 180.0 + 1e-9, step)
    el = np.arange(-90.0, 90.0 + 1e-9, step)
    AZ, EL = np.meshgrid(az, el)
    dirs = dirs_from_az_el(AZ, EL).reshape(-1, 3)
    origins = np.zeros_like(dirs)
    locs, idx, _ = mesh.ray.intersects_location(origins, dirs, multiple_hits=True)
    r = np.full(len(dirs), np.nan)
    if len(idx):
        dist = np.einsum("ij,ij->i", locs, dirs[idx])
        ok = (dist > 20) & (dist < MAX_RADIUS)
        np.fmax.at(r, idx[ok], dist[ok])
    r = r.reshape(AZ.shape)
    missing = np.isnan(r)
    coverage = 1.0 - missing[EL > 0].mean()  # obere Hälfte: dort sitzen die Polster
    if missing.all():
        raise ScanError("scan_no_head", "Um den markierten Punkt herum wurde keine Kopfoberfläche gefunden.")
    if missing.any():
        _, (ii, jj) = distance_transform_edt(missing, return_indices=True)
        r = r[ii, jj]
    r = median_filter(r, size=3, mode="wrap")  # Ausreißer und Scan-Rauschen glätten
    return az, el, r, float(coverage)


def head_from_scan(mesh, marks, circumference, subdivisions=5):
    """Liefert (Kopf-Mesh im Kopf-Koordinatensystem, Infos, Radiustabelle für die Oberfläche)."""
    origin, rot, ear_dist = scan_frame(marks)
    m = mesh.copy()
    m.vertices = (np.asarray(m.vertices) - origin) @ rot.T
    eyes_z = float(rot[2] @ (np.asarray(marks["augen"], float) - origin))

    # Größe: zuerst grob über den Ohrabstand, dann genau über den Kopfumfang
    s = EAR_CANAL_DIST / ear_dist
    perim = None
    for _ in range(5):
        brow = s * eyes_z + EYE_BELOW_BROW
        raw = slice_circumference(m, brow / s, (MAX_RADIUS - 15) / s)
        if raw is None:
            raise ScanError("scan_slice", "Auf Augenbrauenhöhe wurde kein geschlossener Kopfumfang gefunden – "
                            "ist der Scan vollständig und sind die Punkte richtig gesetzt?")
        perim = raw * s
        s *= circumference / perim
    m.vertices = np.asarray(m.vertices) * s

    # Hintergrund und Körper entfernen: nur Flächen nahe am Kopf behalten
    v = np.asarray(m.vertices)
    keep_v = (np.linalg.norm(v, axis=1) < MAX_RADIUS) & (v[:, 2] > CHIN_LIMIT)
    faces = m.faces[keep_v[m.faces].all(axis=1)]
    if len(faces) < 100:
        raise ScanError("scan_no_head", "Um den markierten Punkt herum wurde keine Kopfoberfläche gefunden.")
    head_part = trimesh.Trimesh(v, faces, process=False)

    step = 1.5
    az, el, r, coverage = _radial_grid(head_part, step)
    interp = RegularGridInterpolator((el, az), r)
    sphere = trimesh.creation.icosphere(subdivisions=subdivisions)
    dirs = sphere.vertices / np.linalg.norm(sphere.vertices, axis=1, keepdims=True)
    a, e = az_el_from_dirs(dirs)
    head = trimesh.Trimesh(dirs * interp(np.stack([e, a], axis=-1))[:, None], sphere.faces, process=True)

    brow = s * eyes_z + EYE_BELOW_BROW
    ring = trimesh.Trimesh(head.vertices, head.faces).section(plane_origin=[0, 0, brow], plane_normal=[0, 0, 1])
    ring_pts = np.asarray(ring.vertices)[:, :2] if ring is not None else np.zeros((0, 2))
    warnings = []
    if coverage < 0.85:
        warnings.append(msg("scan_holes", f"Der Scan deckt nur {coverage:.0%} des Oberkopfs ab – Lücken wurden "
                            "geschätzt. Am besten rundherum und von oben filmen.", pct=round(coverage * 100)))
    real_ear = ear_dist * s
    if not 100 <= real_ear <= 190:
        warnings.append(msg("scan_ears", f"Die Ohrpunkte liegen {real_ear:.0f} mm auseinander (üblich 120–160 mm) – "
                            "bitte Markierungen und Kopfumfang prüfen.", mm=round(real_ear)))
    info = {
        "quelle": "scan",
        "laenge_mm": round(float(np.ptp(ring_pts[:, 1])), 1) if len(ring_pts) else None,
        "breite_mm": round(float(np.ptp(ring_pts[:, 0])), 1) if len(ring_pts) else None,
        "umfang_modell_mm": round(_hull_perimeter(ring_pts) or 0.0, 1),
        "ohr_bis_scheitel_mm": round(float(head.vertices[:, 2].max()), 1),
        "augen_ueber_ohr_mm": round(s * eyes_z, 1),
        "ohrabstand_mm": round(real_ear, 1),
        "abdeckung": round(coverage, 3),
        "skalierung": float(s),
        "warnungen": warnings,
    }
    radial = {"step": step, "n_az": len(az), "n_el": len(el), "r": np.round(r, 2).ravel().tolist()}
    return head, info, radial
