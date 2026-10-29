"""Polsterteile erzeugen.

Jedes Polster ist eine gewölbte Schale über einem Raster von Richtungen um die
Zonenmitte: innen liegt sie am Kopf an, außen an der Helm-Innenseite (abzüglich
Klettband). Dadurch ist jedes Teil automatisch geschlossen (wasserdicht) druckbar.
"""
from dataclasses import dataclass

import numpy as np
import trimesh
from scipy.ndimage import distance_transform_edt, gaussian_filter

from .rays import first_hits
from .zones import Zone, glasses_band


@dataclass
class PadParams:
    thickness: float = 10.0     # Ziel-Mindestdicke des Polsters
    compression: float = 2.0    # so weit drückt das Polster in den Kopf (straffer Sitz)
    mount_gap: float = 3.0      # Platz für Klettband zwischen Polster und Helm
    min_thickness: float = 4.0
    max_thickness: float = 30.0
    cell: float = 1.2           # Rasterweite in mm
    edge_radius: float = 4.0    # Abrundung der Innenkante
    channel_spacing: float = 22.0  # Lüftungskanäle auf der Innenseite (0 = aus)
    channel_width: float = 6.0
    channel_depth: float = 3.0
    glasses: bool = False       # Rinne für Brillenbügel in Polstern, die an der Schläfe liegen
    glasses_depth: float = 5.0

    @property
    def fit_gap(self):
        """Abstand Kopf -> Helm, den die Skalierung mindestens herstellen muss."""
        return self.thickness - self.compression + self.mount_gap


@dataclass
class PadResult:
    zone: Zone
    mesh: trimesh.Trimesh          # in Kopf-Koordinaten
    print_mesh: trimesh.Trimesh    # zum Drucken ausgerichtet (Helmseite unten)
    stats: dict
    back_r: float = 0.0            # Abstand der Rückseite (Helmseite) in der Polstermitte
    back_dir: np.ndarray | None = None
    file_stem: str = ""            # Dateiname ohne Endung, mit Nummer (z.B. "01_stirn")
    grid: dict | None = None       # Raster (Richtungen, Dicke, mittlerer Radius) für das 2D-Schnittmuster


def _fill_nan(a):
    bad = ~np.isfinite(a)
    if bad.all():
        return a
    if bad.any():
        _, (ii, jj) = distance_transform_edt(bad, return_indices=True)
        a = a[ii, jj]
    return a


def _grid_faces(nb, na, off=0, flip=False):
    idx = np.arange(nb * na).reshape(nb, na) + off
    a, b = idx[:-1, :-1].ravel(), idx[:-1, 1:].ravel()
    c, d = idx[1:, 1:].ravel(), idx[1:, :-1].ravel()
    if flip:
        return np.concatenate([np.stack([a, c, b], 1), np.stack([a, d, c], 1)])
    return np.concatenate([np.stack([a, b, c], 1), np.stack([a, c, d], 1)])


def _boundary_loop(nb, na):
    loop = [(0, j) for j in range(na)]
    loop += [(i, na - 1) for i in range(1, nb)]
    loop += [(nb - 1, j) for j in range(na - 2, -1, -1)]
    loop += [(i, 0) for i in range(nb - 2, 0, -1)]
    return np.array([i * na + j for i, j in loop])


def build_pad(zone: Zone, head, helmet, p: PadParams) -> PadResult:
    dc, _, _ = zone.frame()
    r_ref = float(first_hits(head, (0, 0, 0), dc[None])[0])
    half_a, half_b = np.radians(zone.half_w), np.radians(zone.half_h)
    len_a, len_b = half_a * r_ref, half_b * r_ref
    na = max(8, int(np.ceil(2 * len_a / p.cell)) + 1)
    nb = max(8, int(np.ceil(2 * len_b / p.cell)) + 1)

    X, Y = np.meshgrid(np.linspace(-1, 1, na), np.linspace(-1, 1, nb))
    # Quadrat -> abgerundete Form (Ellipsen-Abbildung, Topologie bleibt ein Raster)
    D, Xm, Ym = zone.dirs(X, Y)

    r_head = _fill_nan(first_hits(head, (0, 0, 0), D))
    r_helm = first_hits(helmet, (0, 0, 0), D)

    r_in = r_head - p.compression
    r_out_raw = r_helm - p.mount_gap
    floating = ~np.isfinite(r_out_raw) | (r_out_raw - r_in > p.max_thickness)
    r_out = np.where(floating, r_in + p.max_thickness, r_out_raw)
    # leicht glätten, aber nie über die echte Helmfläche hinaus
    r_out = np.minimum(gaussian_filter(r_out, 1.0), np.where(floating, np.inf, r_out))
    tight = r_out - r_in < p.min_thickness
    r_out = np.maximum(r_out, r_in + p.min_thickness)
    thick = r_out - r_in

    # Innenseite formen: Lüftungskanäle + abgerundete Kante (Dicke bleibt >= 2 mm)
    lift = np.zeros_like(r_in)
    if p.channel_spacing > 0:
        xa = Xm * len_a
        dist = np.abs((xa + p.channel_spacing / 2) % p.channel_spacing - p.channel_spacing / 2)
        prof = np.where(dist < p.channel_width / 2, 0.5 * (1 + np.cos(np.pi * dist / (p.channel_width / 2))), 0)
        lift += prof * np.minimum(p.channel_depth, thick - 4.0).clip(0)
    edge = np.minimum((1 - np.abs(X)) * len_a, (1 - np.abs(Y)) * len_b)
    t = 1 - np.clip(edge / p.edge_radius, 0, 1)
    lift = np.maximum(lift, p.edge_radius * (1 - np.sqrt(1 - t ** 2)))
    if p.glasses:
        lift = np.maximum(lift, glasses_band(D) * np.minimum(p.glasses_depth, thick - 2.0).clip(0))
    r_in = r_in + np.minimum(lift, thick - 2.0)

    inner = (D * r_in[..., None]).reshape(-1, 3)
    outer = (D * r_out[..., None]).reshape(-1, 3)
    n = nb * na
    loop = _boundary_loop(nb, na)
    p_, q_ = loop, np.roll(loop, -1)
    walls = np.concatenate([np.stack([q_, p_, p_ + n], 1), np.stack([q_, p_ + n, q_ + n], 1)])
    faces = np.concatenate([_grid_faces(nb, na), _grid_faces(nb, na, off=n, flip=True), walls])
    mesh = trimesh.Trimesh(np.vstack([inner, outer]), faces, process=True)
    if mesh.volume < 0:
        mesh.invert()

    pm = print_orient(mesh, dc)
    ic, jc = nb // 2, na // 2
    stats = {
        "dicke_min_mm": round(float(thick.min()), 1),
        "dicke_mittel_mm": round(float(thick.mean()), 1),
        "dicke_max_mm": round(float(thick.max()), 1),
        "zu_eng_anteil": round(float(tight.mean()), 3),
        "ohne_helmkontakt_anteil": round(float(floating.mean()), 3),
        "masse_mm": [round(float(v), 1) for v in pm.extents],
        "wasserdicht": bool(mesh.is_watertight),
        "volumen_cm3": round(float(mesh.volume) / 1000, 1),
    }
    grid = {"D": D, "thick": thick, "r_mid": r_out - thick / 2}  # Dicke ohne Kanäle/Rundung
    return PadResult(zone, mesh, pm, stats, back_r=float(r_out[ic, jc]), back_dir=D[ic, jc], grid=grid)


def print_orient(mesh, dc):
    """Druckausrichtung: Helmseite nach unten, auf das Druckbett gelegt."""
    pm = mesh.copy()
    pm.apply_transform(trimesh.geometry.align_vectors(dc, [0, 0, -1]))
    lo, hi = pm.bounds
    pm.apply_translation([-(lo[0] + hi[0]) / 2, -(lo[1] + hi[1]) / 2, -lo[2]])
    return pm
