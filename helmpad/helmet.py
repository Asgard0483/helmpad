"""Helm-STL laden und in das Kopf-Koordinatensystem drehen."""
import numpy as np
import trimesh

AXES = {
    "+x": (1, 0, 0), "-x": (-1, 0, 0),
    "+y": (0, 1, 0), "-y": (0, -1, 0),
    "+z": (0, 0, 1), "-z": (0, 0, -1),
}
UNITS = {"mm": 1.0, "cm": 10.0, "m": 1000.0, "in": 25.4}


def axes_rotation(up="+z", front="-y"):
    """Drehmatrix, die `up` -> +Z und `front` (Gesichtsseite) -> +Y bringt."""
    u = np.array(AXES[up], float)
    f = np.array(AXES[front], float)
    if abs(u @ f) > 1e-9:
        raise ValueError("'oben' und 'vorne' müssen senkrecht zueinander stehen")
    return np.stack([np.cross(f, u), f, u])


def clean_rotation(m):
    """Beliebige 3x3-Matrix auf die nächste echte Drehung bringen (keine Spiegelung/Verzerrung)."""
    u, _, vt = np.linalg.svd(np.asarray(m, float).reshape(3, 3))
    r = u @ vt
    if np.linalg.det(r) < 0:
        raise ValueError("Die Ausrichtung enthält eine Spiegelung")
    return r


def to_head_frame(points, rotation, units="mm"):
    """Punkte aus Datei-Koordinaten in dieselben Koordinaten wie der geladene Helm umrechnen."""
    return (np.asarray(points, float) @ np.asarray(rotation).T) * UNITS[units]


def repair_helmet(mesh):
    """Offene Helm-Meshes schließen, damit Gravur und Teilung funktionieren.

    Jeder Körper (Schale, Visier, Anbauten) wird einzeln mit MeshFix repariert und danach zu einem
    sauberen Volumen vereinigt. Gibt (Mesh, Status) zurück: 'dicht' (war schon geschlossen),
    'repariert', 'keine_wand' (Helm ist nur eine Fläche ohne Wandstärke – Reparatur würde den
    Hohlraum füllen) oder 'fehlgeschlagen'.
    """
    if mesh.is_watertight:
        return mesh, "dicht"
    try:
        import manifold3d as m3d
        import pymeshfix
        from .marks import _to_manifold, _to_trimesh, stl_safe
    except ImportError:
        return mesh, "fehlgeschlagen"

    bodies = []
    for part in mesh.split(only_watertight=False, repair=False):
        if part.is_watertight:
            bodies.append(part)
        elif len(part.faces) >= 20:  # kleinste Splitter sind Exportmüll
            v, f = pymeshfix.clean_from_arrays(np.ascontiguousarray(part.vertices, np.float64),
                                               np.ascontiguousarray(part.faces, np.int32),
                                               remove_smallest_components=False)
            fixed = trimesh.Trimesh(v, f)
            if not fixed.is_watertight:
                return mesh, "fehlgeschlagen"
            bodies.append(fixed)
    if not bodies:
        return mesh, "fehlgeschlagen"
    for b in bodies:
        if b.volume < 0:
            b.invert()
    # Eine Schale ist nur ein kleiner Teil ihrer Hülle; ist das Ergebnis fast massiv, wurde die Öffnung zugeschüttet
    if sum(b.volume for b in bodies) > 0.5 * mesh.convex_hull.volume:
        return mesh, "keine_wand"
    man = m3d.Manifold.batch_boolean([_to_manifold(b) for b in bodies], m3d.OpType.Add)
    if man.status() != m3d.Error.NoError or man.is_empty():
        return mesh, "fehlgeschlagen"
    out = _to_trimesh(man)
    return (out, "repariert") if stl_safe(out) else (mesh, "fehlgeschlagen")


def load_helmet(path, up="+z", front="-y", units="mm", rotation=None):
    """Lädt die STL und dreht sie in das Kopf-Koordinatensystem (X rechts, Y vorne, Z oben).

    `rotation` (3x3) hat Vorrang; sonst wird über `up`/`front` gedreht. Standard ist die
    Blender-Konvention: Z oben, Gesicht schaut nach -Y.
    """
    mesh = trimesh.load(path, force="mesh")
    if not isinstance(mesh, trimesh.Trimesh) or len(mesh.faces) == 0:
        raise ValueError(f"Keine Dreiecke in {path} gefunden")
    rot = np.eye(4)
    rot[:3, :3] = clean_rotation(rotation) if rotation is not None else axes_rotation(up, front)
    mesh.apply_transform(rot)
    mesh.apply_scale(UNITS[units])
    mesh.merge_vertices()
    mesh.update_faces(mesh.nondegenerate_faces())
    # einheitliche Orientierung, Normalen nach außen; sonst sähe ein umgestülptes Mesh für
    # Gravur und Teilung wie ein Körper mit negativem Volumen aus
    mesh.fix_normals()
    if mesh.is_watertight and mesh.volume < 0:
        mesh.invert()
    return mesh, rot[:3, :3]
