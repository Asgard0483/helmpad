"""Einbauhilfen: Rahmen + Nummer jedes Polsters in der Helm-Innenseite, Nummer auf der Polster-Rückseite.

Die Markierungen werden als Rille eingraviert (braucht ein geschlossenes Helm-Mesh) oder,
falls das nicht geht, als flacher Steg aufgesetzt. Slicer verschmelzen den Steg beim Drucken
automatisch mit dem Helm.
"""
import manifold3d as m3d
import numpy as np
import trimesh

from .rays import first_hits

# Sieben-Segment-Ziffern: Segmente a (oben), b (rechts oben), c (rechts unten), d (unten),
# e (links unten), f (links oben), g (Mitte)
SEGMENTS = {
    "0": "abcdef", "1": "bc", "2": "abged", "3": "abgcd", "4": "fgbc",
    "5": "afgcd", "6": "afgedc", "7": "abc", "8": "abcdefg", "9": "abcdfg",
}
DIGIT_W, DIGIT_H, BAR = 6.0, 10.0, 1.4


def _to_manifold(mesh):
    return m3d.Manifold(m3d.Mesh(vert_properties=np.asarray(mesh.vertices, np.float32),
                                 tri_verts=np.asarray(mesh.faces, np.uint32)))


def _raw_trimesh(man):
    m = man.to_mesh()
    # process=False: Manifold liefert bereits ein sauberes Mesh; trimesh würde beim Zusammenführen
    # fast gleicher Punkte sonst einzelne Kanten mehrfach belegen (nicht mehr wasserdicht).
    return trimesh.Trimesh(np.asarray(m.vert_properties)[:, :3], np.asarray(m.tri_verts), process=False)


def stl_safe(mesh):
    """Bleibt das Mesh nach Speichern + Laden als STL wasserdicht? (STL speichert nur 32-Bit-Punkte,
    beim Laden verschmelzen fast gleiche Punkte – hauchdünne Splitter machen das Teil dann undicht.)"""
    tri = np.asarray(mesh.vertices, np.float32)[np.asarray(mesh.faces)]
    return trimesh.Trimesh(**trimesh.triangles.to_kwargs(tri), process=True).is_watertight


def _to_trimesh(man):
    """Manifold -> trimesh; entfernt notfalls Splitter (< 1 µm), damit das Teil auch als STL dicht bleibt."""
    mesh = _raw_trimesh(man)
    if not stl_safe(mesh):
        cleaned = _raw_trimesh(man.simplify(0.001))
        if stl_safe(cleaned):
            return cleaned
    return mesh


def _band(points, dirs, r_lo, r_hi, width, closed):
    """Vierkant-Strang entlang einer Linie auf der Fläche; radial von r_lo bis r_hi (relativ zur Linie)."""
    n = len(points)
    t = np.roll(points, -1, 0) - np.roll(points, 1, 0)
    if not closed:
        t[0], t[-1] = points[1] - points[0], points[-1] - points[-2]
    side = np.cross(dirs, t)
    side /= np.linalg.norm(side, axis=1, keepdims=True)
    hw = width / 2
    corners = [points + dirs * r_lo - side * hw, points + dirs * r_lo + side * hw,
               points + dirs * r_hi + side * hw, points + dirs * r_hi - side * hw]
    verts = np.stack(corners, 1).reshape(-1, 3)  # Punkt i, Ecke k -> 4*i + k
    faces = []
    segs = n if closed else n - 1
    for i in range(segs):
        j = (i + 1) % n
        for k in range(4):
            a, b = 4 * i + k, 4 * i + (k + 1) % 4
            c, d = 4 * j + (k + 1) % 4, 4 * j + k
            faces += [[a, b, c], [a, c, d]]
    if not closed:
        faces += [[0, 2, 1], [0, 3, 2]]
        e = 4 * (n - 1)
        faces += [[e, e + 1, e + 2], [e, e + 2, e + 3]]
    mesh = trimesh.Trimesh(verts, np.array(faces), process=True)
    mesh.fix_normals()  # einheitliche Orientierung, nach außen
    return mesh


def number_boxes(text, center, right, up, normal, r_lo, r_hi, project=None, piece=1.0):
    """Zahl als Sieben-Segment-Balken auf einer Fläche, lesbar mit Blick entlang `normal`
    (Ansichtsrichtung in die Fläche hinein; rechts = `right`, oben = `up`).

    `project` (optional): bildet Punkte der Tangentialebene (N, 3) auf die echte, gewölbte Fläche
    ab (NaN = keine Fläche). Jeder Balken folgt dann als gebogener Strang der Fläche, so klappt
    die Gravur auch auf schräg stehenden oder stark gewölbten Wänden. Ohne `project` gerade Quader.
    """
    seg_pos = {  # Mitte (x, y) und ob waagerecht
        "a": (0, DIGIT_H / 2, True), "g": (0, 0, True), "d": (0, -DIGIT_H / 2, True),
        "f": (-DIGIT_W / 2, DIGIT_H / 4, False), "b": (DIGIT_W / 2, DIGIT_H / 4, False),
        "e": (-DIGIT_W / 2, -DIGIT_H / 4, False), "c": (DIGIT_W / 2, -DIGIT_H / 4, False),
    }
    pitch = DIGIT_W + 3.0
    x0 = -(len(text) - 1) * pitch / 2
    parts = []
    for i, ch in enumerate(text):
        for k, s in enumerate(SEGMENTS.get(ch, "")):
            x, y, horiz = seg_pos[s]
            length = DIGIT_W + BAR if horiz else DIGIT_H / 2 + BAR
            # jeder Balken minimal anders tief und breit: überlappende Balken (Ecken, b/c, e/f)
            # hätten sonst deckungsgleiche Flächen -> Splitter, Mesh nicht mehr wasserdicht
            lo, hi = r_lo - 0.03 * k, r_hi + 0.03 * k
            bar = BAR + 0.02 * k
            along = right if horiz else up
            mid = center + right * (x0 + i * pitch + x) + up * y
            if project is None:
                n_rot = normal if np.dot(np.cross(right, up), normal) > 0 else -normal
                tf = np.eye(4)
                tf[:3, :3] = np.stack([right, up, n_rot], 1)
                tf[:3, 3] = mid + normal * (lo + hi) / 2
                size = (length, bar, hi - lo) if horiz else (bar, length, hi - lo)
                parts.append(trimesh.creation.box(extents=size, transform=tf))
                continue
            n = max(2, int(np.ceil(length / piece)) + 1)
            line = mid + np.linspace(-length / 2, length / 2, n)[:, None] * along
            pts = project(line)
            if not np.all(np.isfinite(pts)):
                return []  # ein fehlender Balken machte aus der Ziffer eine andere (8 -> 0) – dann keine Nummer
            # Tiefe entlang der Blickrichtung in die Fläche, Breite quer zum Balken
            parts.append(_band(pts, np.repeat(normal[None], n, 0), lo, hi, bar, closed=False))
    return parts


def _radial_projector(mesh, from_outside=False):
    """Punkte entlang ihrer Richtung vom Ursprung auf die Fläche des Meshes schieben."""
    def project(pts):
        d = pts / np.linalg.norm(pts, axis=1, keepdims=True)
        if from_outside:  # z.B. Polster-Rückseite: von außen nach innen der erste Treffer
            r = 1000.0 - first_hits_from(mesh, d * 1000.0, -d)
        else:
            r = first_hits(mesh, (0, 0, 0), d)
        return d * r[:, None]
    return project


def first_hits_from(mesh, origins, dirs):
    """Wie first_hits, aber mit eigenem Startpunkt je Strahl."""
    locs, idx, _ = mesh.ray.intersects_location(origins, dirs, multiple_hits=False)
    dist = np.full(len(dirs), np.nan)
    if len(idx):
        dist[idx] = np.einsum("ij,ij->i", locs - origins[idx], dirs[idx])
    return dist


def ring_marks(helmet, dirs, radius, r_lo, r_hi, width=1.2):
    """Kreis-Markierungen auf der Helm-Innenseite (Position der Magnethalter)."""
    parts = []
    for d in dirs:
        rc = first_hits(helmet, (0, 0, 0), d[None])[0]
        if not np.isfinite(rc):
            continue
        e1 = np.cross(d, [0, 0, 1.0])
        if np.linalg.norm(e1) < 1e-6:
            e1 = np.array([1.0, 0, 0])
        e1 /= np.linalg.norm(e1)
        e2 = np.cross(d, e1)
        t = np.linspace(0, 2 * np.pi, 48, endpoint=False)
        ring = d * rc + radius * (np.cos(t)[:, None] * e1 + np.sin(t)[:, None] * e2)
        rd = ring / np.linalg.norm(ring, axis=1, keepdims=True)
        r = first_hits(helmet, (0, 0, 0), rd)
        if not np.all(np.isfinite(r)):
            continue
        parts.append(_band(rd * r[:, None], rd, r_lo, r_hi, width, closed=True))
    return parts


def helmet_marks(helmet, pads, mode="rille", margin=2.0, width=1.6, depth=0.6, height=0.8,
                 ring_dirs=None, ring_radius=8.0):
    """Rahmen + Nummer jedes Polsters in die Helm-Innenseite bringen.

    `ring_dirs`: zusätzlich Kreise an diesen Stellen (Position der Magnethalter), Radius `ring_radius`.

    Gibt (Helm-Mesh, tatsächlich verwendeter Modus) zurück. mode: 'rille', 'steg' oder 'keine'.
    """
    if mode == "keine" or not pads:
        return helmet, "keine"
    # Rille: Schnittkörper reicht von 1 mm vor der Fläche bis `depth` in die Wand
    # Steg: Körper ragt `height` aus der Fläche heraus und steckt 0.5 mm in der Wand
    r_lo, r_hi = (-1.0, depth) if mode == "rille" else (-height, 0.5)
    project = _radial_projector(helmet)
    parts = []
    for num, pad in enumerate(pads, 1):
        z = pad.zone
        dc, ea, eb = z.frame()
        # je Polster minimal andere Tiefe/Breite: sich kreuzende Rahmen hätten sonst deckungsgleiche Flächen
        jit = (num % 4) * 0.025
        f_lo, f_hi, f_w = r_lo - jit, r_hi + jit, width + jit
        r_ref = float(first_hits(helmet, (0, 0, 0), dc[None])[0])
        if not np.isfinite(r_ref):
            r_ref = pad.back_r
        extra = np.degrees((margin + width / 2) / max(r_ref, 1.0))
        # Rand des Quadrats im Uhrzeigersinn abtasten, dann auf die Zonenform abbilden
        s = np.linspace(-1, 1, 50, endpoint=False)
        X = np.concatenate([s, np.ones_like(s), -s, -np.ones_like(s)])
        Y = np.concatenate([-np.ones_like(s), s, np.ones_like(s), -s])
        D, _, _ = z.dirs(X, Y, extra_deg=extra)
        r = first_hits(helmet, (0, 0, 0), D)
        ok = np.isfinite(r) & (r < 2.5 * r_ref)
        pts = D * np.where(ok, r, 0)[:, None]
        if ok.all():
            parts.append(_band(pts, D, f_lo, f_hi, f_w, closed=True))
        else:  # Öffnungen im Helm: Rahmen in zusammenhängende Stücke teilen
            idx = np.flatnonzero(ok)
            if len(idx):
                start = np.flatnonzero(~ok)[0]
                order = np.roll(np.arange(len(ok)), -start)
                run = []
                for i in list(order) + [order[0]]:
                    if ok[i]:
                        run.append(i)
                    elif run:
                        if len(run) >= 3:
                            parts.append(_band(pts[run], D[run], f_lo, f_hi, f_w, closed=False))
                        run = []
                if len(run) >= 3:
                    parts.append(_band(pts[run], D[run], f_lo, f_hi, f_w, closed=False))
        rc = first_hits(helmet, (0, 0, 0), dc[None])[0]
        if np.isfinite(rc):
            # von innen gelesen: Blick entlang dc, rechts = ea, oben = eb
            parts += number_boxes(str(num), dc * rc, ea, eb, dc, r_lo, r_hi, project=project)

    if ring_dirs:
        parts += ring_marks(helmet, ring_dirs, ring_radius, r_lo - 0.05, r_hi + 0.05)

    if mode == "rille":
        base = _to_manifold(helmet)
        if base.status() == m3d.Error.NoError:
            tools = m3d.Manifold.batch_boolean([_to_manifold(p) for p in parts], m3d.OpType.Add)
            res = base - tools
            if res.status() == m3d.Error.NoError and not res.is_empty():
                return _to_trimesh(res), "rille"
        # Helm-Mesh ist nicht geschlossen -> als Steg aufsetzen
        return helmet_marks(helmet, pads, "steg", margin, width, depth, height, ring_dirs, ring_radius)
    return trimesh.util.concatenate([helmet] + parts), "steg"


def engrave_pad_number(pad, num, depth=0.6):
    """Nummer in die Rückseite (Helmseite) eines Polsters gravieren. Gibt das neue Mesh zurück."""
    dc, ea, eb = pad.zone.frame()
    # von hinten gelesen: Blick entgegen dc -> rechts = -ea
    boxes = number_boxes(str(num), pad.back_dir * pad.back_r, -ea, eb, -pad.back_dir, -1.0, depth,
                         project=_radial_projector(pad.mesh, from_outside=True))
    base = _to_manifold(pad.mesh)
    if base.status() != m3d.Error.NoError:
        return pad.mesh
    res = base - m3d.Manifold.batch_boolean([_to_manifold(b) for b in boxes], m3d.OpType.Add)
    if res.status() != m3d.Error.NoError or res.is_empty():
        return pad.mesh
    out = _to_trimesh(res)
    # lieber ohne Nummer als ein undichtes Druckteil
    return out if stl_safe(out) else pad.mesh
