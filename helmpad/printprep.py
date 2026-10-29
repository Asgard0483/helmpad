"""Druckvorbereitung: Kenngrößen je Teil, Teile auf Druckplatten verteilen, 3MF für Bambu Studio & Co.

Material und Zeit werden in der Oberfläche aus Volumen + Oberfläche geschätzt (Preis, Füllung und
Druckgeschwindigkeit sind dort einstellbar). Das ist eine grobe Schätzung – der Slicer weiß es genauer.
"""
from pathlib import Path

import numpy as np
import trimesh


def part_stats(mesh):
    """Volumen (cm³) und Oberfläche (cm²) eines Druckteils."""
    return {"volumen_cm3": round(float(abs(mesh.volume)) / 1000, 2), "flaeche_cm2": round(float(mesh.area) / 100, 2)}


def pack_plates(items, bed, gap=6.0, margin=5.0):
    """Teile (Name, Mesh) mit einfachem Regal-Verfahren auf Platten der Größe `bed` verteilen.

    Liefert eine Liste von Platten, jede eine Liste (Name, Mesh) mit bereits verschobenen Meshes.
    Teile, die gar nicht auf eine Platte passen, bekommen eine eigene Platte.
    """
    bx, by = bed[0] - 2 * margin, bed[1] - 2 * margin
    prepared = []
    for name, mesh in items:
        m = mesh.copy()
        ex = m.extents
        if (ex[0] > bx or ex[1] > by) and ex[1] <= bx and ex[0] <= by:  # um 90° drehen, wenn es dann passt
            m.apply_transform(trimesh.transformations.rotation_matrix(np.pi / 2, [0, 0, 1]))
        lo = m.bounds[0]
        m.apply_translation(-lo)
        prepared.append((name, m))
    prepared.sort(key=lambda it: -it[1].extents[1])

    plates, current = [], []
    x = y = shelf_h = 0.0
    for name, m in prepared:
        w, h = m.extents[0], m.extents[1]
        if x + w > bx:  # neue Zeile
            x, y, shelf_h = 0.0, y + shelf_h + gap, 0.0
        if y + h > by and current:  # neue Platte
            plates.append(current)
            current, x, y, shelf_h = [], 0.0, 0.0, 0.0
        m.apply_translation([margin + x, margin + y, 0])
        current.append((name, m))
        x += w + gap
        shelf_h = max(shelf_h, h)
    if current:
        plates.append(current)
    return plates


_CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
 <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
 <Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>
</Types>"""
_RELS = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
 <Relationship Target="/3D/3dmodel.model" Id="rel0" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>
</Relationships>"""


def write_3mf(path, items):
    """Minimaler 3MF-Schreiber (Kern-Spezifikation): jedes (Name, Mesh) wird ein eigenes Objekt."""
    from xml.sax.saxutils import quoteattr
    import zipfile

    parts = ['<?xml version="1.0" encoding="UTF-8"?>\n'
             '<model unit="millimeter" xml:lang="de-DE" xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">\n'
             ' <metadata name="Application">HelmPad</metadata>\n <resources>\n']
    for i, (name, m) in enumerate(items, 1):
        parts.append(f'  <object id="{i}" type="model" name={quoteattr(name)}><mesh><vertices>\n')
        parts.append("".join(f'<vertex x="{x:.4f}" y="{y:.4f}" z="{z:.4f}"/>' for x, y, z in m.vertices))
        parts.append("\n</vertices><triangles>\n")
        parts.append("".join(f'<triangle v1="{a}" v2="{b}" v3="{c}"/>' for a, b, c in m.faces))
        parts.append("\n</triangles></mesh></object>\n")
    parts.append(" </resources>\n <build>\n")
    parts += [f'  <item objectid="{i}"/>\n' for i in range(1, len(items) + 1)]
    parts.append(" </build>\n</model>\n")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", _CONTENT_TYPES)
        z.writestr("_rels/.rels", _RELS)
        z.writestr("3D/3dmodel.model", "".join(parts))


def export_3mf_plates(items, bed, out_dir, prefix):
    """Je Druckplatte eine 3MF-Datei; jedes Teil bleibt ein eigenes Objekt (einzeln im Slicer wählbar)."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    files = []
    for i, plate in enumerate(pack_plates(items, bed), 1):
        path = out_dir / f"{prefix}_platte_{i}.3mf"
        write_3mf(path, plate)
        files.append({"datei": path.name, "teile": [n for n, _ in plate]})
    return files
