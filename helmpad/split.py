"""Helm in druckbare Teile schneiden, mit Dübellöchern an den Schnittflächen und passenden Dübeln.

Jeder Schnitt ist eine Ebene im Kopf-Koordinatensystem (X rechts, Y vorne, Z oben). Entlang der
Schnittkante werden Dübel gesetzt: Ist die Wand dick genug, sitzt das Loch mittig in der Wand,
sonst bekommt die Wand innen einen kleinen Verstärkungsblock. Polsterflächen werden ausgespart.
"""
from dataclasses import dataclass, field

import manifold3d as m3d
import numpy as np
import trimesh

from .messages import msg
from .marks import _to_manifold, _to_trimesh

AXIS = {"x": np.array([1.0, 0, 0]), "y": np.array([0, 1.0, 0]), "z": np.array([0, 0, 1.0])}
IN_PLANE = {"x": ("y", "z"), "y": ("x", "z"), "z": ("x", "y")}
SIDE_NAMES = {"x": ("rechts", "links"), "y": ("vorne", "hinten"), "z": ("oben", "unten")}


@dataclass
class Cut:
    axis: str        # 'x' (links/rechts), 'y' (vorne/hinten), 'z' (oben/unten)
    pos: float = 0.0  # Lage der Ebene in mm (Kopf-Koordinaten)

    @property
    def normal(self):
        return AXIS[self.axis]

    @property
    def plane(self):
        # minimal versetzt: Ebenen genau durch Eckpunkte des Modells (z.B. bei 0) erzeugen beim
        # Schneiden hauchdünne Splitter, dann wären die Teile nicht mehr wasserdicht
        return self.pos + 0.0137


@dataclass
class DowelParams:
    diameter: float = 5.0     # Dübel-Durchmesser
    length: float = 16.0      # Gesamtlänge (je Hälfte steckt die Hälfte im Teil)
    spacing: float = 55.0     # Abstand entlang der Schnittkante
    clearance: float = 0.15   # Spiel je Seite im Loch
    min_wall: float = 1.2     # Restwand um das Loch
    end_margin: float = 12.0  # Abstand zu Enden der Schnittkante (Öffnungen, Rand)


@dataclass
class Part:
    name: str
    mesh: trimesh.Trimesh        # in Kopf-Koordinaten
    print_mesh: trimesh.Trimesh  # druckfertig: größte Schnittfläche unten
    sides: list = field(default_factory=list)  # [(Cut, +1/-1)]
    fits: bool = True


def _cylinder(center, axis, radius, length, sections=32):
    tf = np.eye(4)
    tf[:3, :3] = trimesh.geometry.align_vectors([0, 0, 1], axis)[:3, :3]
    tf[:3, 3] = center
    return trimesh.creation.cylinder(radius=radius, height=length, sections=sections, transform=tf)


def _wall_hits(helmet, origin, dirs):
    """Je Richtung: Abstand zur ersten (Innenseite) und zweiten (Außenseite) Wandfläche."""
    origins = np.repeat(origin[None], len(dirs), 0)
    locs, idx, _ = helmet.ray.intersects_location(origins, dirs, multiple_hits=True)
    r1 = np.full(len(dirs), np.nan)
    r2 = np.full(len(dirs), np.nan)
    if len(idx):
        dist = np.einsum("ij,ij->i", locs - origins[idx], dirs[idx])
        order = np.lexsort((dist, idx))
        idx, dist = idx[order], dist[order]
        first = np.r_[True, idx[1:] != idx[:-1]]
        r1[idx[first]] = dist[first]
        second = np.r_[False, (idx[1:] == idx[:-1]) & first[:-1]]
        r2[idx[second]] = dist[second]
    return r1, r2


def dowel_positions(helmet, cut: Cut, zones, dp: DowelParams, step_deg=1.0):
    """Dübelpositionen entlang einer Schnittkante: Liste von (Mitte, braucht_Verstärkung, Wanddicke)."""
    n = cut.normal
    origin = n * cut.plane
    u, v = AXIS[IN_PLANE[cut.axis][0]], AXIS[IN_PLANE[cut.axis][1]]
    th = np.radians(np.arange(0, 360, step_deg))
    dirs = np.cos(th)[:, None] * u + np.sin(th)[:, None] * v
    r1, r2 = _wall_hits(helmet, origin, dirs)
    ok = np.isfinite(r1) & np.isfinite(r2) & (r2 - r1 > 0.6) & (r1 < 400)
    boss_r = dp.diameter / 2 + 2.0
    thick_enough = r2 - r1 >= dp.diameter + 2 * (dp.clearance + dp.min_wall)
    rc = np.where(thick_enough, (r1 + r2) / 2, r2 - 0.4 - boss_r)
    centers = origin + dirs * rc[:, None]
    # nicht unter Polstern (Richtung vom Kopf-Ursprung aus)
    cd = centers / np.linalg.norm(centers, axis=1, keepdims=True)
    under_pad = np.zeros(len(ok), bool)
    for z in zones:
        under_pad |= z.contains(cd, margin_deg=5.0)
    # unter Polstern nur, wenn die Wand dick genug ist: dann braucht es keinen Block, der stört
    ok &= ~under_pad | thick_enough

    # zusammenhängende Bögen der Schnittkante, darauf in festen Abständen verteilen
    out = []
    if not ok.any():
        return out
    start = int(np.argmin(ok)) if not ok.all() else 0
    order = np.roll(np.arange(len(ok)), -start)
    runs, run = [], []
    for i in order:
        if ok[i]:
            run.append(i)
        elif run:
            runs.append(run)
            run = []
    if run:
        runs.append(run)
    for run in runs:
        pts = centers[run]
        seg = np.linalg.norm(np.diff(pts, axis=0), axis=1) if len(pts) > 1 else np.zeros(0)
        seg = np.where(seg > 15, 0, seg)  # Sprünge (andere Wand) nicht mitzählen
        s = np.r_[0, np.cumsum(seg)]
        usable = s[-1] - 2 * dp.end_margin
        if usable < 0:
            continue
        count = max(1, int(usable // dp.spacing) + 1)
        targets = np.linspace(dp.end_margin, s[-1] - dp.end_margin, count) if count > 1 else [s[-1] / 2]
        for t in targets:
            k = run[int(np.argmin(np.abs(s - t)))]
            out.append((centers[k], not bool(thick_enough[k]), float(r2[k] - r1[k])))
    return out


def dowel_mesh(dp: DowelParams, chamfer=0.6):
    """Ein Dübel mit angefasten Enden, stehend (Z) für den Druck."""
    r, L = dp.diameter / 2, dp.length
    body = m3d.Manifold.cylinder(L - 2 * chamfer, r, r, 48).translate([0, 0, chamfer])
    bottom = m3d.Manifold.cylinder(chamfer, r - chamfer, r, 48)
    top = m3d.Manifold.cylinder(chamfer, r, r - chamfer, 48).translate([0, 0, L - chamfer])
    return _to_trimesh(body + bottom + top)


def dowel_set(dp: DowelParams, count):
    one = dowel_mesh(dp)
    cols = int(np.ceil(np.sqrt(count)))
    pitch = dp.diameter + 4
    return trimesh.util.concatenate(
        [one.copy().apply_translation([(i % cols) * pitch, (i // cols) * pitch, 0]) for i in range(count)])


def _print_orient(mesh, sides, bed=None):
    """Eine Schnittfläche nach unten legen: die größte, die das Teil in den Bauraum bringt
    (passt keine, die größte)."""
    faces = []
    for cut, sign in sides:
        outward = -sign * cut.normal  # Schnittfläche zeigt zur abgetrennten Seite
        on_plane = np.abs(mesh.triangles_center @ cut.normal - cut.plane) < 0.05
        facing = mesh.face_normals @ outward > 0.999
        faces.append((mesh.area_faces[on_plane & facing].sum(), outward))
    faces.sort(key=lambda f: -f[0])

    def place(outward):
        pm = mesh.copy()
        if outward is not None:
            pm.apply_transform(trimesh.geometry.align_vectors(outward, [0, 0, -1]))
        lo, hi = pm.bounds
        pm.apply_translation([-(lo[0] + hi[0]) / 2, -(lo[1] + hi[1]) / 2, -lo[2]])
        return pm

    candidates = [place(o) for a, o in faces if a > 0] or [place(None)]
    if bed is not None:
        for pm in candidates:
            if fits_bed(pm, bed):
                return pm
    return candidates[0]


def fits_bed(mesh, bed):
    ex = mesh.extents
    bx, by, bz = bed
    return bool(ex[2] <= bz and ((ex[0] <= bx and ex[1] <= by) or (ex[0] <= by and ex[1] <= bx)))


def split_helmet(helmet, cuts, zones, dp: DowelParams, bed=(256, 256, 256)):
    """Schneidet den Helm, setzt Dübellöcher. Gibt (Teile, Dübelmitten, Warnungen) zurück."""
    warnings = []
    base = _to_manifold(helmet)
    if base.status() != m3d.Error.NoError:
        return None, [], [msg("split_not_closed", "Der Helm ist kein geschlossenes Mesh und kann nicht geteilt werden. "
                              "Bitte die STL vorher reparieren (z.B. in Bambu Studio/PrusaSlicer: „Modell reparieren“).")]

    bosses, holes, dowels = [], [], []
    keep_off = dp.diameter / 2 + 2.0 + 3.0  # Abstand zu anderen Schnitten (Dübel nicht mit durchschneiden)
    for cut in cuts:
        positions = [(c, b, t) for c, b, t in dowel_positions(helmet, cut, zones, dp)
                     if all(abs(c @ o.normal - o.plane) > keep_off for o in cuts if o is not cut)]
        if not positions:
            warnings.append(msg("no_dowel", f"Schnitt {cut.axis.upper()}: keine Stelle für Dübel gefunden "
                                "(alles unter Polstern oder offen).", axis=cut.axis))
        for c, needs_boss, _ in positions:
            if needs_boss:
                bosses.append(_cylinder(c, cut.normal, dp.diameter / 2 + 2.0, dp.length + 4))
            holes.append(_cylinder(c, cut.normal, dp.diameter / 2 + dp.clearance, dp.length + 1.0))
            dowels.append((c, cut.normal))
        thin = sum(b for _, b, _ in positions)
        if thin:
            wall = round(dp.diameter + 2 * (dp.clearance + dp.min_wall), 1)
            warnings.append(msg("thin_wall", f"Schnitt {cut.axis.upper()}: Wand an {thin} von {len(positions)} Dübelstellen "
                                f"dünner als {wall} mm – dort wurde innen ein Verstärkungsblock ergänzt.",
                                axis=cut.axis, n=int(thin), total=len(positions), mm=wall))
    if bosses:
        base = base + m3d.Manifold.batch_boolean([_to_manifold(b) for b in bosses], m3d.OpType.Add)
    if holes:
        base = base - m3d.Manifold.batch_boolean([_to_manifold(h) for h in holes], m3d.OpType.Add)

    pieces = [(base, [])]
    for cut in cuts:
        nxt = []
        for man, sides in pieces:
            pos, neg = man.split_by_plane(tuple(cut.normal), float(cut.plane))
            for part, sign in ((pos, 1), (neg, -1)):
                if not part.is_empty() and abs(part.volume()) > 1000:  # Reste < 1 cm³ verwerfen
                    nxt.append((part, sides + [(cut, sign)]))
        pieces = nxt
    if not pieces:
        return None, [], warnings + [msg("split_not_closed", "Der Helm ist kein geschlossenes Mesh und kann nicht "
                                         "geteilt werden. Bitte die STL vorher reparieren.")]

    parts = []
    for i, (man, sides) in enumerate(pieces, 1):
        label = "_".join(SIDE_NAMES[c.axis][0 if s > 0 else 1] for c, s in sides) or "ganz"
        mesh = _to_trimesh(man)
        pm = _print_orient(mesh, sides, bed)
        part = Part(f"helm_teil_{i}_{label}", mesh, pm, sides, fits_bed(pm, bed))
        parts.append(part)
    for p in parts:
        if not p.fits:
            dims = " × ".join(f"{v:.0f}" for v in p.print_mesh.extents)
            bed_txt = " × ".join(f"{v:.0f}" for v in bed)
            warnings.append(msg("part_too_big", f"{p.name}: {dims} mm passt nicht in den Bauraum {bed_txt} mm.",
                                nr=parts.index(p) + 1, sides=[[c.axis, s] for c, s in p.sides], dims=dims, bed=bed_txt))
    return parts, dowels, warnings


def auto_cuts(helmet, zones, dp, bed):
    """Kleinste Teilung finden, bei der jedes Teil auf das Druckbett passt."""
    lo, hi = helmet.bounds
    mid = (lo + hi) / 2
    ext = hi - lo

    def best(axis, center):
        # Lage nahe der Mitte wählen, an der die meisten Dübel Platz haben (Polster weichen aus)
        k = "xyz".index(axis)
        cands = [center + f * ext[k] for f in (0, -0.06, 0.06, -0.12, 0.12, -0.18, 0.18)]
        counts = [len(dowel_positions(helmet, Cut(axis, c), zones, dp)) for c in cands]
        return Cut(axis, float(cands[int(np.argmax(counts))]))

    y_cut, z_cut = best("y", float(mid[1])), best("z", float(mid[2]))
    options = [[], [Cut("x", 0.0)], [y_cut], [Cut("x", 0.0), y_cut], [Cut("x", 0.0), y_cut, z_cut]]
    for cuts in options:
        # schnelle Vorprüfung über die Bounding-Box der Teile, mit derselben Regel wie beim Drucken:
        # eine Schnittfläche liegt unten (Höhe = Ausdehnung entlang dieser Schnittachse)
        boxes = [(lo.copy(), hi.copy(), set())]
        for c in cuts:
            k = "xyz".index(c.axis)
            nb = []
            for a, b, axes in boxes:
                if a[k] < c.pos < b[k]:
                    b1, a2 = b.copy(), a.copy()
                    b1[k], a2[k] = c.pos, c.pos
                    nb += [(a, b1, axes | {k}), (a2, b, axes | {k})]
                else:
                    nb.append((a, b, axes))
            boxes = nb

        def box_fits(ext, axes):
            for k in axes or {2}:  # ohne Schnittfläche wird das Teil so gedruckt, wie es steht
                foot = [ext[j] for j in range(3) if j != k]
                if ext[k] <= bed[2] and ((foot[0] <= bed[0] and foot[1] <= bed[1]) or
                                         (foot[0] <= bed[1] and foot[1] <= bed[0])):
                    return True
            return False

        if all(box_fits(b - a, axes) for a, b, axes in boxes):
            return cuts
    return options[-1]
