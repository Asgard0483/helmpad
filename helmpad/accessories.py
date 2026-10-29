"""Zubehör: Magnet-Befestigung, Kinnriemen-Halter, Lüfterhalter, Mikrofon-Clip.

Alle Teile werden mit manifold3d als geschlossene Körper gebaut (druckfertig, flach auf dem Bett).
Halter mit flacher Klebefläche werden mit flexiblem Kleber (z.B. E6000) in den Helm geklebt;
ihre Position wird in die Helm-Innenseite graviert.
"""
from dataclasses import dataclass

import manifold3d as m3d
import numpy as np
import trimesh

from .marks import _to_trimesh, first_hits_from

SEG = 64


@dataclass
class MagnetParams:
    diameter: float = 10.0   # Scheibenmagnet, z.B. 10 x 3 mm
    height: float = 3.0
    fit: float = 0.15        # Spiel je Seite in der Tasche

    @property
    def cup_height(self):
        """Höhe des Magnethalters im Helm = Abstand zwischen Polster und Helm."""
        return self.height + 1.0


def _cyl(h, r, z0=0.0):
    return m3d.Manifold.cylinder(h, r, r, SEG).translate([0, 0, z0])


def _box(x, y, z, cx=0.0, cy=0.0, z0=0.0):
    return m3d.Manifold.cube([x, y, z]).translate([cx - x / 2, cy - y / 2, z0])


def magnet_cup(mp: MagnetParams):
    """Halter, der in den Helm geklebt wird; der Magnet liegt oben bündig."""
    r_in = mp.diameter / 2 + mp.fit
    body = _cyl(mp.cup_height, r_in + 1.6)
    pocket = _cyl(mp.height + 0.6, r_in, z0=mp.cup_height - mp.height - 0.1)
    return _to_trimesh(body - pocket)


def magnet_positions(pad, count=None):
    """Richtungen für die Magnete eines Polsters: zwei entlang der längeren Seite, bei kleinen Polstern einer."""
    z = pad.zone
    dc, ea, eb = z.frame()
    long_ax, half = (ea, z.half_w) if z.half_w >= z.half_h else (eb, z.half_h)
    r = pad.back_r
    length = 2 * np.radians(half) * r
    n = count or (2 if length >= 45 else 1)
    if n == 1:
        offs = [0.0]
    else:
        offs = [-0.45, 0.45]
    dirs = []
    for f in offs:
        a = np.radians(half) * f
        d = np.cos(a) * dc + np.sin(a) * long_ax
        dirs.append(d / np.linalg.norm(d))
    return dirs


def magnet_pockets(pad, mp: MagnetParams, dirs):
    """Taschen in der Polster-Rückseite (Helmseite), Magnet schließt bündig ab."""
    r_in = mp.diameter / 2 + mp.fit
    tools = []
    depth = mp.height + 0.2
    for d in dirs:
        # Rückseite von außen anpeilen (erster Treffer entgegen d)
        hit = 1000.0 - first_hits_from(pad.mesh, (d * 1000.0)[None], -d[None])[0]
        if not np.isfinite(hit):
            continue
        surface = d * hit
        # Zylinder entlang d: 1 mm außerhalb der Fläche bis `depth` ins Polster hinein
        tf = trimesh.geometry.align_vectors([0, 0, 1], d)
        tf[:3, 3] = surface + d * (1.0 - depth) / 2
        tools.append(trimesh.creation.cylinder(radius=r_in, height=depth + 1.0, sections=SEG, transform=tf))
    return tools


def subtract(mesh, tools):
    """Werkzeuge vom Mesh abziehen; klappt das nicht sauber (oder wäre die STL undicht), bleibt das Original."""
    from .marks import _to_manifold, stl_safe
    if not tools:
        return mesh
    base = _to_manifold(mesh)
    if base.status() != m3d.Error.NoError:
        return mesh
    res = base - m3d.Manifold.batch_boolean([_to_manifold(t) for t in tools], m3d.OpType.Add)
    if res.status() != m3d.Error.NoError or res.is_empty():
        return mesh
    out = _to_trimesh(res)
    return out if stl_safe(out) else mesh


def strap_anchor(strap_width=25.0):
    """Kinnriemen-Halter: Klebeplatte mit Bügel, durch den der Gurt gefädelt wird."""
    slot_w, slot_h = strap_width + 1.5, 3.2
    plate = _box(slot_w + 12, 22, 2.4)
    bridge = _box(slot_w + 8, 10, slot_h + 2.4, z0=2.4)
    tunnel = _box(slot_w, 12, slot_h, z0=2.4)
    return _to_trimesh(plate + bridge - tunnel)


def fan_mount(size=40):
    """Lüfterhalter für 30- oder 40-mm-Lüfter: Rahmen mit Luftöffnung und Schraublöchern."""
    hole_pitch = {30: 24.0, 40: 32.0, 25: 20.0}.get(int(size), size * 0.8)
    outer = size + 8
    plate = _box(outer, outer, 2.4)
    rim = _box(outer, outer, 4.0, z0=2.4) - _box(size + 0.6, size + 0.6, 4.0, z0=2.4)
    opening = _cyl(3.0, size / 2 - 1.0, z0=-0.3)
    holes = [_cyl(10, 1.4, z0=-1).translate([sx * hole_pitch / 2, sy * hole_pitch / 2, 0])
             for sx in (-1, 1) for sy in (-1, 1)]
    return _to_trimesh(plate + rim - opening - m3d.Manifold.batch_boolean(holes, m3d.OpType.Add))


def mic_clip(diameter=6.0):
    """Mikrofon-Clip: Klebeplatte mit offenem Ring, in den der Mikrofonarm einrastet."""
    r_in = diameter / 2 + 0.2
    wall = 1.8
    plate = _box(diameter + 14, 14, 2.0)
    ring_c = 2.0 + r_in + 0.2
    ring = (m3d.Manifold.cylinder(12, r_in + wall, r_in + wall, SEG)
            - m3d.Manifold.cylinder(14, r_in, r_in, SEG).translate([0, 0, -1]))
    ring = ring.rotate([90, 0, 0]).translate([0, 6, ring_c])
    opening = _box(diameter * 0.75, 14, r_in + wall + 1, z0=ring_c + r_in * 0.45)
    return _to_trimesh(plate + ring - opening)


def part_set(mesh, count, gap=4.0):
    """Mehrere gleiche Teile nebeneinander (für einen Druckauftrag)."""
    ex = mesh.extents
    cols = int(np.ceil(np.sqrt(count)))
    items = []
    for i in range(count):
        m = mesh.copy()
        lo = m.bounds[0]
        m.apply_translation([-lo[0] + (i % cols) * (ex[0] + gap), -lo[1] + (i // cols) * (ex[1] + gap), -lo[2]])
        items.append(m)
    return trimesh.util.concatenate(items)
