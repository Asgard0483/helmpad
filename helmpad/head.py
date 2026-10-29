"""Parametrisches Kopfmodell aus Körpermaßen.

Der Schädel wird als Superquadrik mit schmalerer Stirn und verjüngtem Kiefer
modelliert. Das reicht für die Polsterzonen (Stirn, Scheitel, Seiten, Hinterkopf);
das Gesicht (Nase, Kinn) ist nur grob angenähert.
"""
from dataclasses import dataclass, asdict

import numpy as np
import trimesh
from scipy.optimize import brentq

P_HORIZ = 2.4   # Exponent im Querschnitt (2 = Ellipse, höher = eckiger)
Q_VERT = 2.2    # Exponent vertikal
FRONT_NARROW = 0.07  # Stirn ist schmaler als der Hinterkopf
JAW_TAPER = 0.30     # Verjüngung Richtung Kinn
CENTER_FWD = 0.03    # Ohrkanal liegt etwas hinter der Kopfmitte (Anteil der Länge)
EYE_BELOW_BROW = 12.0  # Pupillen liegen etwa so weit unter der Augenbraue


@dataclass
class HeadMeasurements:
    circumference: float                 # Kopfumfang über Stirn/Augenbrauen und Hinterkopf
    length: float | None = None          # Stirn (Glabella) bis Hinterkopf
    width: float | None = None           # größte Kopfbreite (über den Ohren)
    ear_to_vertex: float | None = None   # Ohrkanal bis Scheitel, senkrecht
    face_height: float | None = None     # Nasenwurzel bis Kinn
    brow_height: float = 22.0            # Augenbraue über Ohrkanal


@dataclass
class HeadShape:
    a: float        # halbe Länge
    b: float        # halbe Breite
    c_up: float     # Augenbraue -> Scheitel
    c_down: float   # Augenbraue -> Kinn
    y0: float
    z0: float

    @property
    def length(self):
        return 2 * self.a

    @property
    def width(self):
        return 2 * self.b

    def implicit(self, pts):
        x, y, z = pts[..., 0], pts[..., 1], pts[..., 2]
        below = np.clip((self.z0 - z) / self.c_down, 0.0, 1.0)
        s = 1.0 - JAW_TAPER * below ** 1.5
        v = (y - self.y0) / (self.a * s)
        b_eff = self.b * (1.0 - FRONT_NARROW * np.clip(v, 0.0, 1.0))
        u = x / (b_eff * s)
        c = np.where(z >= self.z0, self.c_up, self.c_down)
        w = (z - self.z0) / c
        return (np.abs(u) ** P_HORIZ + np.abs(v) ** P_HORIZ) ** (Q_VERT / P_HORIZ) + np.abs(w) ** Q_VERT

    def radius(self, dirs, iters=40):
        """Abstand vom Ursprung zur Oberfläche je Richtung (Bisektion)."""
        lo = np.zeros(dirs.shape[:-1])
        hi = np.full(dirs.shape[:-1], 400.0)
        for _ in range(iters):
            mid = 0.5 * (lo + hi)
            inside = self.implicit(dirs * mid[..., None]) < 1.0
            lo = np.where(inside, mid, lo)
            hi = np.where(inside, hi, mid)
        return 0.5 * (lo + hi)

    def circumference(self, n=4000):
        """Umfang des Querschnitts auf Augenbrauenhöhe."""
        t = np.linspace(0, 2 * np.pi, n, endpoint=False)
        d = np.stack([np.sin(t), np.cos(t), np.zeros_like(t)], axis=1)
        # Strahlen von (0, y0, z0) aus in der Messebene
        lo = np.zeros(n)
        hi = np.full(n, 400.0)
        origin = np.array([0.0, self.y0, self.z0])
        for _ in range(40):
            mid = 0.5 * (lo + hi)
            inside = self.implicit(origin + d * mid[:, None]) < 1.0
            lo = np.where(inside, mid, lo)
            hi = np.where(inside, hi, mid)
        pts = origin + d * (0.5 * (lo + hi))[:, None]
        return float(np.linalg.norm(np.roll(pts, -1, axis=0) - pts, axis=1).sum())


def solve_shape(m: HeadMeasurements) -> HeadShape:
    C = m.circumference
    ear_to_vertex = m.ear_to_vertex or 0.228 * C
    face_height = m.face_height or 0.21 * C
    z0 = m.brow_height
    c_up = ear_to_vertex - z0
    c_down = face_height

    def shape(L, W):
        return HeadShape(L / 2, W / 2, c_up, c_down, CENTER_FWD * L, z0)

    if m.length and m.width:
        L, W = m.length, m.width
    elif m.length:
        L = m.length
        W = brentq(lambda w: shape(L, w).circumference() - C, 60, 300)
    elif m.width:
        W = m.width
        L = brentq(lambda l: shape(l, W).circumference() - C, 80, 350)
    else:
        k = brentq(lambda k: shape(k, 0.80 * k).circumference() - C, 80, 350)
        L, W = k, 0.80 * k
    return shape(L, W)


def build_head(m: HeadMeasurements, subdivisions=5):
    """Liefert (Mesh, HeadShape, Infos) für die gegebenen Maße."""
    shape = solve_shape(m)
    sphere = trimesh.creation.icosphere(subdivisions=subdivisions)
    dirs = sphere.vertices / np.linalg.norm(sphere.vertices, axis=1, keepdims=True)
    verts = dirs * shape.radius(dirs)[:, None]
    mesh = trimesh.Trimesh(verts, sphere.faces, process=True)
    info = {
        "eingabe": asdict(m),
        "laenge_mm": round(shape.length, 1),
        "breite_mm": round(shape.width, 1),
        "umfang_modell_mm": round(shape.circumference(), 1),
        "ohr_bis_scheitel_mm": round(shape.z0 + shape.c_up, 1),
        "augen_ueber_ohr_mm": round(shape.z0 - EYE_BELOW_BROW, 1),
    }
    return mesh, shape, info
