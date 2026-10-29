"""Automatische Ausrichtung einer Helm-STL: oben, vorne, links/rechts erkennen.

1. Halsöffnung: Von einem Punkt im Hohlraum gehen Prüfstrahlen in alle Richtungen. „Unten“ ist die Richtung,
   in der viele Strahlen ins Freie entkommen, während die Gegenrichtung (Scheitel) geschlossen ist.
2. Gesichtsseite: Strahlen, die seitlich entkommen (Sehschlitz, Visier, Gesichtsfeld), zeigen nach vorne.
3. Symmetrie: Helme sind spiegelsymmetrisch. Gesucht wird die Ebene durch „oben“, an der das gespiegelte Modell
   am besten auf sich selbst passt -> trennt links und rechts und richtet „vorne“ genau aus.
4. Ohne Gesichtsöffnung (geschlossenes Visier) entscheidet die Form: das Kinn reicht meist tiefer als der Nacken.

Die Neigung nach vorne/hinten ist bei schrägen Halsöffnungen oder offenen Gesichtern nur ungefähr; dafür gibt es
die Markierungen (Augen, Scheitel). Die gemeldete Sicherheit sagt, wie sehr man dem Vorschlag trauen kann.

Ergebnis ist eine Drehmatrix Datei -> Kopf-Koordinaten (Zeilen X rechts, Y vorne, Z oben) samt Sicherheit.
"""
import numpy as np
from scipy.spatial import cKDTree

from .rays import fibonacci_sphere, first_hits

R = np.radians
NECK_CONE = np.cos(R(40))
CROWN_CONE = np.cos(R(55))


def _inner_point(mesh, sample):
    """Punkt im Hohlraum: Flächenschwerpunkt (unabhängig von der Lage), notfalls Richtung Box-Mitte verschoben."""
    dirs = fibonacci_sphere(400)
    c0 = sample.mean(axis=0)
    box = mesh.bounds.mean(axis=0)
    for w in (0.0, 0.3, 0.6, 1.0):
        p = (1 - w) * c0 + w * box
        if np.isfinite(first_hits(mesh, p, dirs)).mean() > 0.5:
            return p
    return c0


def _horizontal(v, up):
    v = v - (v @ up) * up
    nrm = np.linalg.norm(v)
    return v / nrm if nrm > 1e-9 else None


def detect_orientation(mesh, n_rays=6000):
    """Liefert dict(rotation=3x3 Zeilen, sicherheit='hoch'|'mittel'|'niedrig', gruende=[…], details={…})."""
    reasons = []
    rng = np.random.default_rng(0)
    sample = np.asarray(mesh.sample(6000, return_index=False))
    size = float(np.linalg.norm(mesh.extents))
    c = _inner_point(mesh, sample)

    # 1) unten: viele Strahlen entkommen, die Gegenrichtung (Scheitel) ist zu
    dirs = fibonacci_sphere(n_rays)
    esc = dirs[~np.isfinite(first_hits(mesh, c, dirs))]
    if len(esc) >= 20:
        cand = fibonacci_sphere(1000)
        cos = cand @ esc.T
        score = (cos > NECK_CONE).sum(axis=1) - 3 * (cos < -CROWN_CONE).sum(axis=1)
        down = cand[int(np.argmax(score))]
        for _ in range(3):  # auf die Mitte der Öffnung nachziehen
            near = esc[esc @ down > NECK_CONE]
            down = near.mean(axis=0) / np.linalg.norm(near.mean(axis=0))
        neck_frac = float((esc @ down > NECK_CONE).sum()) / n_rays
        crown_closed = float((esc @ down < -CROWN_CONE).sum()) / n_rays < 0.003
    else:
        _, vecs = np.linalg.eigh(np.cov((sample - sample.mean(0)).T))
        down, neck_frac, crown_closed = -vecs[:, 0], 0.0, False
    up = -down
    if neck_frac >= 0.05 and crown_closed:
        reasons.append("neck")

    # 2) Gesichtsseite aus seitlichen Öffnungen (mehr als 60° von „unten“)
    side_esc = esc[esc @ down < np.cos(R(60))] if len(esc) else np.zeros((0, 3))
    face_vec = side_esc.sum(axis=0) / n_rays if len(side_esc) else np.zeros(3)
    face_dir = _horizontal(face_vec, up)
    has_face = face_dir is not None and np.linalg.norm(face_vec - (face_vec @ up) * up) >= 0.003
    # getrennte Öffnungen? (zwischen Hals und Gesicht ist Wand) – sonst ist die Neigung unsicher
    band = ((esc @ down < np.cos(R(45))) & (esc @ down > np.cos(R(60)))).sum() / n_rays if len(esc) else 0.0
    merged = has_face and band > 0.015

    # 3) Mittelebene durch „oben“
    pts = sample[rng.permutation(len(sample))[:3000]]
    tree = cKDTree(pts)
    centroid = pts.mean(axis=0)

    def sym_error(n):
        d = (pts - centroid) @ n
        return float(np.median(tree.query(pts - 2 * d[:, None] * n)[0]))

    e1 = _horizontal(np.array([1.0, 0, 0]), up)
    if e1 is None:
        e1 = _horizontal(np.array([0, 1.0, 0]), up)
    e2 = np.cross(up, e1)
    normal = lambda a: np.cos(a) * e1 + np.sin(a) * e2  # noqa: E731
    angles = R(np.arange(0, 180, 2.0))
    errs = np.array([sym_error(normal(a)) for a in angles])
    if has_face:
        # nur Ebenen, die ungefähr durch die Gesichtsöffnung gehen
        side0 = np.cross(face_dir, up)
        ok = np.abs([normal(a) @ side0 for a in angles]) > np.cos(R(30))
        errs = np.where(ok, errs, np.inf)
    a0 = angles[int(np.argmin(errs))]
    fine = a0 + R(np.arange(-2, 2.01, 0.25))
    ferrs = [sym_error(normal(a)) for a in fine]
    a_best = fine[int(np.argmin(ferrs))]
    sym_rel = min(ferrs) / size
    if sym_rel < 0.012:
        reasons.append("symmetry")
    side = normal(a_best)
    fwd_axis = np.cross(up, side)

    # 4) vorne festlegen
    if has_face:
        front = fwd_axis if face_dir @ fwd_axis >= 0 else -fwd_axis
        reasons.append("face_opening")
    else:
        # ohne Gesichtsöffnung: bei zwei gleich guten Ebenen ist vorne-hinten die längere Achse
        alt = a_best + np.pi / 2
        alt_axis = np.cross(up, normal(alt))
        if sym_error(normal(alt)) < 1.25 * min(ferrs) and np.ptp(sample @ alt_axis) > np.ptp(sample @ fwd_axis):
            side, fwd_axis = normal(alt), alt_axis
        # Kinn: vorne reicht tiefer als der Nacken (nur ein schwacher Hinweis)
        h = (sample - c) @ up
        f = (sample - c) @ fwd_axis
        lo_front = np.percentile(h[f > 0], 2) if (f > 0).any() else 0.0
        lo_back = np.percentile(h[f < 0], 2) if (f < 0).any() else 0.0
        chin = (lo_back - lo_front) / size
        front = fwd_axis if chin >= 0 else -fwd_axis
        if abs(chin) > 0.05:
            reasons.append("chin")

    right = np.cross(front, up)  # rechtshändig: X = vorne × oben
    rot = np.stack([right / np.linalg.norm(right), front / np.linalg.norm(front), up])

    sure_front = "face_opening" in reasons or "chin" in reasons
    if "neck" in reasons and "symmetry" in reasons and "face_opening" in reasons and not merged:
        confidence = "hoch"
    elif "neck" in reasons and sure_front and not merged:
        confidence = "mittel"
    else:
        confidence = "niedrig"
    if merged:
        reasons.append("open_face")
    return {"rotation": rot.round(6).tolist(), "sicherheit": confidence, "gruende": reasons,
            "details": {"hals_anteil": round(neck_frac, 4), "uebergang": round(float(band), 4),
                        "symmetrie_fehler_rel": round(sym_rel, 4)}}
