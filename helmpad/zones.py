"""Polsterzonen: Bereiche am Kopf, an denen je ein Polsterteil sitzt."""
import json
import re
from dataclasses import dataclass, asdict

import numpy as np

from .rays import dirs_from_az_el


@dataclass
class Zone:
    name: str
    az: float          # Mitte, Azimut in Grad (0 = vorne, 90 = rechts, 180 = hinten)
    el: float          # Mitte, Elevation in Grad über Ohrkanal-Höhe
    half_w: float      # halbe Breite in Grad (quer)
    half_h: float      # halbe Höhe in Grad (längs)
    roundness: float = 0.6  # 0 = Rechteck, 1 = Ellipse
    roll: float = 0.0       # Drehung um die Zonenmitte in Grad

    def frame(self):
        """Lokales Koordinatensystem: Mittelrichtung, Querrichtung, Längsrichtung."""
        dc = dirs_from_az_el(self.az, self.el)
        up = np.array([0.0, 0.0, 1.0])
        ea = np.cross(dc, up)
        if np.linalg.norm(ea) < 1e-6:  # Zone genau am Scheitel: Grenzwert für el -> 90°
            az = np.radians(self.az)
            ea = np.array([np.cos(az), -np.sin(az), 0.0])
        ea /= np.linalg.norm(ea)
        eb = np.cross(ea, dc)
        if self.roll:
            r = np.radians(self.roll)
            ea, eb = np.cos(r) * ea + np.sin(r) * eb, np.cos(r) * eb - np.sin(r) * ea
        return dc, ea, eb

    def dirs(self, X, Y, extra_deg=0.0):
        """Rasterkoordinaten X, Y in [-1, 1] -> Richtungen auf der (abgerundeten) Zonenfläche.

        `extra_deg` vergrößert die Zone rundum (z.B. für einen Rahmen um das Polster).
        """
        dc, ea, eb = self.frame()
        r = self.roundness
        Xm = (1 - r) * X + r * X * np.sqrt(1 - Y ** 2 / 2)
        Ym = (1 - r) * Y + r * Y * np.sqrt(1 - X ** 2 / 2)
        A = Xm * np.radians(self.half_w + extra_deg)
        B = Ym * np.radians(self.half_h + extra_deg)
        D = (np.cos(B) * np.cos(A))[..., None] * dc + (np.cos(B) * np.sin(A))[..., None] * ea \
            + np.sin(B)[..., None] * eb
        return D, Xm, Ym

    def local_angles(self, dirs):
        """Richtungen -> (quer, längs) in Grad relativ zur Zonenmitte."""
        dc, ea, eb = self.frame()
        a = np.degrees(np.arctan2(dirs @ ea, dirs @ dc))
        b = np.degrees(np.arcsin(np.clip(dirs @ eb, -1, 1)))
        return a, b

    def contains(self, dirs, margin_deg=0.0):
        a, b = self.local_angles(dirs)
        dc, _, _ = self.frame()
        return (dirs @ dc > 0) & (np.abs(a) <= self.half_w + margin_deg) & (np.abs(b) <= self.half_h + margin_deg)


# Ohren reichen bis ca. 25° über den Ohrkanal -> Seitenpolster sitzen darüber.
DEFAULT_ZONES = [
    Zone("stirn", az=0, el=24, half_w=30, half_h=13),
    Zone("scheitel", az=0, el=90, half_w=22, half_h=26, roundness=0.9),
    Zone("seite_rechts", az=90, el=46, half_w=26, half_h=12),
    Zone("seite_links", az=-90, el=46, half_w=26, half_h=12),
    Zone("hinterkopf_oben", az=180, el=34, half_w=32, half_h=11),
    Zone("hinterkopf_unten", az=180, el=4, half_w=30, half_h=11),
]

PRESETS = {
    "wenig": ("3 Teile – schnell eingebaut", [
        Zone("stirn", az=0, el=22, half_w=40, half_h=14),
        Zone("scheitel", az=0, el=90, half_w=24, half_h=30, roundness=0.9),
        Zone("hinterkopf", az=180, el=20, half_w=38, half_h=22),
    ]),
    "standard": ("6 Teile – empfohlen", DEFAULT_ZONES),
    "viel": ("10 Teile – kleine Drucke, feine Anpassung", [
        Zone("stirn_rechts", az=16, el=24, half_w=14, half_h=13),
        Zone("stirn_links", az=-16, el=24, half_w=14, half_h=13),
        Zone("scheitel_vorne", az=0, el=76, half_w=22, half_h=12, roundness=0.8),
        Zone("scheitel_hinten", az=180, el=76, half_w=22, half_h=12, roundness=0.8),
        Zone("seite_rechts", az=90, el=46, half_w=26, half_h=12),
        Zone("seite_links", az=-90, el=46, half_w=26, half_h=12),
        Zone("hinterkopf_oben_rechts", az=164, el=34, half_w=15, half_h=11),
        Zone("hinterkopf_oben_links", az=-164, el=34, half_w=15, half_h=11),
        Zone("hinterkopf_unten_rechts", az=165, el=4, half_w=14, half_h=11),
        Zone("hinterkopf_unten_links", az=-165, el=4, half_w=14, half_h=11),
    ]),
}


# Verlauf der Brillenbügel (Schläfe -> Ohr), in Grad relativ zum Kopf-Ursprung (Ohrkanal-Mitte)
GLASSES_AZ = (25.0, 110.0)   # |Azimut| von der Schläfe bis hinter das Ohr
GLASSES_EL = (0.0, 20.0)     # Elevation: Augenhöhe bis Ohransatz


def glasses_band(dirs, soft=3.0):
    """0..1 je Richtung: wie stark sie im Bereich der Brillenbügel liegt (weicher Rand von `soft` Grad)."""
    from .rays import az_el_from_dirs
    az, el = az_el_from_dirs(dirs)
    a = np.abs(az)

    def ramp(x, lo, hi):
        return np.clip(np.minimum(x - lo, hi - x) / soft + 0.5, 0, 1)

    return ramp(a, *GLASSES_AZ) * ramp(el, *GLASSES_EL)


def safe_names(names, max_len=40):
    """Polsternamen dateisicher und eindeutig machen (sie werden zu Dateinamen)."""
    out, seen = [], set()
    for name in names:
        n = re.sub(r"[^\w\-]+", "_", str(name)).strip("_")[:max_len] or "polster"
        base, k = n, 2
        while n.lower() in seen:
            n = f"{base}_{k}"
            k += 1
        seen.add(n.lower())
        out.append(n)
    return out


def load_zones(path=None):
    if path is None:
        return list(DEFAULT_ZONES)
    with open(path, encoding="utf-8") as f:
        zones = [Zone(**z) for z in json.load(f)]
    for z, n in zip(zones, safe_names(z.name for z in zones)):
        z.name = n
    return zones


def save_zones(zones, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump([asdict(z) for z in zones], f, indent=2, ensure_ascii=False)
