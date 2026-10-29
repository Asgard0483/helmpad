"""Kommandozeile: python -m helmpad --helm helm.stl --umfang 570 --out ausgabe"""
import argparse
import sys
from pathlib import Path

from .head import HeadMeasurements
from .helmet import AXES, UNITS
from .pads import PadParams
from .pipeline import run
from .split import Cut, DowelParams
from .zones import load_zones


def main(argv=None):
    ap = argparse.ArgumentParser(prog="helmpad", description="Erzeugt druckbare Polster für einen Cosplay-Helm.")
    ap.add_argument("--helm", required=True, help="Helm-STL (oder OBJ/PLY/3MF)")
    ap.add_argument("--out", default="ausgabe", help="Ausgabeordner")

    g = ap.add_argument_group("Kopfmaße (mm)")
    g.add_argument("--umfang", type=float, required=True, help="Kopfumfang über Stirn und Hinterkopf")
    g.add_argument("--laenge", type=float, help="Stirn (zwischen den Augenbrauen) bis Hinterkopf")
    g.add_argument("--breite", type=float, help="größte Kopfbreite")
    g.add_argument("--ohr-scheitel", type=float, help="Ohrkanal bis Scheitel, senkrecht gemessen")
    g.add_argument("--gesicht", type=float, help="Nasenwurzel bis Kinn")

    g = ap.add_argument_group("Helm")
    g.add_argument("--oben", default="+z", choices=AXES, help="Achse, die im STL nach oben zeigt (Standard +z)")
    g.add_argument("--vorne", default="-y", choices=AXES, help="Achse, in die das Gesicht zeigt (Standard -y wie Blender)")
    g.add_argument("--einheit", default="mm", choices=UNITS)
    g.add_argument("--skalierung", default="auto", help="'auto' oder fester Faktor, z.B. 1.0")
    g.add_argument("--proportional", action="store_true", help="Breite/Länge/Höhe einzeln an die Kopfform anpassen")
    g.add_argument("--markierung", default="rille", choices=("rille", "steg", "keine"),
                   help="Einbau-Markierungen (Rahmen + Nummer) in der Helm-Innenseite")
    g.add_argument("--versatz", type=float, nargs=3, default=(0, 0, 0), metavar=("X", "Y", "Z"),
                   help="Kopf im Helm verschieben (mm): X rechts, Y vorne, Z oben")

    g = ap.add_argument_group("Polster")
    g.add_argument("--dicke", type=float, default=10.0, help="Mindestdicke der Polster (Standard 10)")
    g.add_argument("--kompression", type=float, default=2.0, help="wie stark das Polster drückt (Standard 2)")
    g.add_argument("--klett", type=float, default=3.0, help="Platz für Klettband (Standard 3)")
    g.add_argument("--kanaele", type=float, default=22.0, help="Abstand der Lüftungskanäle, 0 = keine")
    g.add_argument("--zonen", help="JSON-Datei mit eigenen Polsterzonen (Vorlage wird in --out abgelegt)")

    g = ap.add_argument_group("Helm teilen")
    g.add_argument("--teilen", default="keine", choices=("keine", "auto"),
                   help="'auto' = so wenige Teile wie nötig für den Bauraum (oder eigene Schnitte mit --schnitt)")
    g.add_argument("--schnitt", nargs=2, action="append", metavar=("ACHSE", "POS"), default=[],
                   help="eigener Schnitt, mehrfach möglich: x = links/rechts, y = vorne/hinten, z = oben/unten; POS in mm")
    g.add_argument("--bauraum", type=float, nargs=3, default=(256, 256, 256), metavar=("X", "Y", "Z"))
    g = ap.add_argument_group("Schaumstoff")
    g.add_argument("--schaum", type=float, metavar="MM",
                   help="2D-Schnittmuster (PDF/SVG/DXF) für Schaumstoff dieser Stärke erzeugen, z.B. 6")
    g.add_argument("--papier", default="A4", choices=("A4", "Letter"))
    g.add_argument("--duebel", type=float, nargs=2, default=(5.0, 16.0), metavar=("DURCHMESSER", "LAENGE"))
    a = ap.parse_args(argv)
    try:
        cuts = [Cut(ax.lower(), float(pos)) for ax, pos in a.schnitt]
        scale = None if a.skalierung == "auto" else float(a.skalierung)
    except ValueError:
        ap.error("--schnitt POS und --skalierung müssen Zahlen sein (oder --skalierung auto)")
    if any(c.axis not in ("x", "y", "z") for c in cuts):
        ap.error("--schnitt: Achse muss x, y oder z sein")

    report, _, _, pads = run(
        a.helm,
        HeadMeasurements(a.umfang, a.laenge, a.breite, a.ohr_scheitel, a.gesicht),
        load_zones(a.zonen),
        PadParams(thickness=a.dicke, compression=a.kompression, mount_gap=a.klett, channel_spacing=a.kanaele),
        a.out, up=a.oben, front=a.vorne, units=a.einheit,
        scale=scale, offset=a.versatz,
        proportional=a.proportional, mark_mode=a.markierung,
        split_mode="manuell" if cuts else a.teilen, cuts=cuts, bed=tuple(a.bauraum),
        dowel=DowelParams(diameter=a.duebel[0], length=a.duebel[1]),
        pattern={"sheet": a.schaum, "paper": a.papier} if a.schaum else None,
    )

    out = Path(a.out)
    print(f"\nHelm skaliert auf {report['helm']['skalierung_prozent']} % -> {out / 'helm_angepasst.stl'}")
    for p in pads:
        s = p.stats
        print(f"  {p.zone.name:18s} {s['masse_mm'][0]:5.0f} x {s['masse_mm'][1]:5.0f} x {s['masse_mm'][2]:4.0f} mm, "
              f"Dicke {s['dicke_min_mm']}-{s['dicke_max_mm']} mm")
    t = report["teilung"]
    if t["teile"]:
        print(f"Helm geteilt in {len(t['teile'])} Teile -> {out / 'helm_teile'}")
        if t["duebel"]:
            print(f"  {t['duebel']['anzahl']} Dübel Ø {t['duebel']['durchmesser_mm']} × {t['duebel']['laenge_mm']} mm")
    elif t.get("hinweis"):
        print(t["hinweis"]["text"])
    for w in report["warnungen"]:
        print("WARNUNG:", w)
    print(f"Vorschau: {out / 'vorschau.html'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
