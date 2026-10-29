"""Erzeugt einen einfachen Test-Helm (absichtlich zu groß und verschoben), Blender-Ausrichtung: Z oben, Gesicht -Y."""
import sys

import manifold3d as m3d
import numpy as np
import trimesh


def ellipsoid(rx, ry, rz, seg=128):
    return m3d.Manifold.sphere(1.0, seg).scale([rx, ry, rz])


def main(path="testdaten/testhelm.stl"):
    shell = ellipsoid(118, 135, 145) - ellipsoid(113, 130, 140)
    shell -= m3d.Manifold.cube([400, 400, 200], center=True).translate([0, 0, -100 - 70])  # Halsöffnung
    shell -= m3d.Manifold.cube([170, 80, 34], center=True).translate([0, -120, -10])       # Visierschlitz
    crest = m3d.Manifold.cube([14, 230, 80], center=True).translate([0, 10, 140])
    shell += (crest ^ ellipsoid(140, 160, 175)) - ellipsoid(113, 130, 140)                 # Kamm oben
    shell = shell.translate([20, -15, 300])  # irgendwo im Raum, wie bei echten STLs

    m = shell.to_mesh()
    mesh = trimesh.Trimesh(np.asarray(m.vert_properties)[:, :3], np.asarray(m.tri_verts))
    mesh.export(path)
    print(path, len(mesh.faces), "Dreiecke, wasserdicht:", mesh.is_watertight, "Maße:", mesh.extents.round(1))


if __name__ == "__main__":
    main(*sys.argv[1:])
