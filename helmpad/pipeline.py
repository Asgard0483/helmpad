"""Kompletter Ablauf: Kopf erzeugen, Helm anpassen, Polster bauen, Dateien schreiben."""
import json
from dataclasses import replace
from pathlib import Path

import trimesh

from . import accessories as acc
from .fit import FitParams, fit_head
from .head import HeadMeasurements, build_head
from .helmet import load_helmet, repair_helmet, to_head_frame
from .marks import engrave_pad_number, helmet_marks
from .messages import msg, texts
from .pads import PadParams, build_pad, print_orient
from .pattern import write_pattern
from .printprep import export_3mf_plates, part_stats
from .split import DowelParams, _cylinder, auto_cuts, dowel_mesh, dowel_set, split_helmet
from .viewer import write_viewer
from .zones import save_zones


def run(helm_path, meas: HeadMeasurements, zones, pp: PadParams, out_dir,
        up="+z", front="-y", units="mm", scale=None, offset=(0, 0, 0), log=print, viewer=True,
        rotation=None, landmarks=None, proportional=False, max_distort=0.12,
        mark_mode="rille", pad_numbers=True,
        split_mode="keine", cuts=None, bed=(256, 256, 256), dowel=None,
        test_piece=True, mount="klett", magnet=None, glasses=False, accessories=None, make_3mf=True,
        pattern=None, head_override=None):
    """`rotation`: 3x3-Drehung Datei -> Kopf-Koordinaten (Vorrang vor up/front).
    `landmarks`: Punkte in Datei-Koordinaten, Schlüssel 'ohr_links', 'ohr_rechts', 'augen', ...
    `proportional`: Breite/Länge/Höhe des Helms einzeln an den Kopf anpassen (max. `max_distort`).
    `mark_mode`: Einbau-Markierungen im Helm: 'rille', 'steg' oder 'keine'.
    `split_mode`: Helm teilen: 'keine', 'auto' (passend zum Bauraum `bed`) oder 'manuell' (`cuts`).
    `test_piece`: kleines Probestück zum Testen von TPU-Härte und Anpressdruck.
    `mount`: Befestigung 'klett' oder 'magnet' (Taschen im Polster + Magnethalter für den Helm).
    `glasses`: Freiraum für Brillenbügel von der Schläfe bis zum Ohr.
    `accessories`: {'riemen': Gurtbreite, 'luefter': 30|40, 'mikrofon': Durchmesser} – jeweils optional.
    `pattern`: {'sheet': Schaumstoffstärke mm, 'paper': 'A4'|'Letter'} – 2D-Schnittmuster als PDF/SVG/DXF.
    `head_override`: (Mesh, Infos) eines gescannten Kopfes (headscan.head_from_scan) statt des Modells aus Maßen.
    """
    out = Path(out_dir)
    (out / "polster").mkdir(parents=True, exist_ok=True)
    save_zones(zones, out / "zonen.json")
    accessories = accessories or {}
    mp = (magnet or acc.MagnetParams()) if mount == "magnet" else None
    # bei Magneten ersetzt der Magnethalter das Klettband als Abstand zwischen Polster und Helm
    pp = replace(pp, glasses=glasses, mount_gap=mp.cup_height if mp else pp.mount_gap)

    scan_warnings = []
    if head_override is not None:
        log("Kopfmodell aus dem Scan übernehmen ...")
        head, head_info = head_override
        head_info = dict(head_info)
        scan_warnings = head_info.pop("warnungen", [])
    else:
        log("Kopfmodell erzeugen ...")
        head, _, head_info = build_head(meas)
    log("Helm laden ...")
    helmet, rot = load_helmet(helm_path, up, front, units, rotation)
    log("Helm prüfen und ggf. reparieren ...")
    helmet, repair_state = repair_helmet(helmet)

    fp = FitParams(gap_pad=pp.fit_gap, offset=tuple(offset), scale=scale, eye_up=head_info["augen_ueber_ohr_mm"],
                   proportional=proportional, max_distort=max_distort, glasses=glasses)
    lm = {k: to_head_frame(v, rot, units) for k, v in (landmarks or {}).items() if v is not None}
    if "ohr_links" in lm and "ohr_rechts" in lm:
        fp.ear_center = (lm["ohr_links"] + lm["ohr_rechts"]) / 2
    if "augen" in lm:
        fp.eye_point = lm["augen"]

    log("Kopf im Helm platzieren und Größe bestimmen ...")
    original_size = helmet.extents.copy()
    fit = fit_head(helmet, head, zones, fp)
    original = helmet.copy()
    original.apply_transform(fit.unscaled_transform())
    helmet.apply_transform(fit.transform())

    pads, magnet_dirs = [], []
    for num, z in enumerate(zones, 1):
        log(f"Polster {num} '{z.name}' ...")
        pad = build_pad(z, head, helmet, pp)
        dirs = acc.magnet_positions(pad) if mp else []
        # bei nur einem Magneten sitzt er in der Mitte – dann dort keine Nummer
        if pad_numbers and len(dirs) != 1:
            pad.mesh = engrave_pad_number(pad, num)
        if dirs:
            pad.mesh = acc.subtract(pad.mesh, acc.magnet_pockets(pad, mp, dirs))
            magnet_dirs += dirs
        pad.print_mesh = print_orient(pad.mesh, z.frame()[0])
        pad.file_stem = f"{num:02d}_{z.name}"
        pad.print_mesh.export(out / "polster" / f"{pad.file_stem}.stl")
        pads.append(pad)

    pattern_info = None
    if pattern:
        log("Schnittmuster für Schaumstoff ...")
        pattern_info = write_pattern(out / "schnittmuster", pads, sheet=float(pattern.get("sheet", 6.0)),
                                     paper=pattern.get("paper", "A4"))

    test_info = None
    if test_piece and zones:
        # Probestück: kleiner Ausschnitt des ersten Polsters, gleiche Dicke, Kanäle und Befestigung
        z0 = zones[0]
        tz = replace(z0, name="probestueck", half_w=min(z0.half_w, 9.0), half_h=min(z0.half_h, 9.0))
        tp = build_pad(tz, head, helmet, pp)
        if mp:
            tp.mesh = acc.subtract(tp.mesh, acc.magnet_pockets(tp, mp, acc.magnet_positions(tp, count=1)))
        tp.print_mesh = print_orient(tp.mesh, tz.frame()[0])
        tp.print_mesh.export(out / "polster" / "00_probestueck.stl")
        test_info = {"datei": "polster/00_probestueck.stl", "masse_mm": [round(float(v), 1) for v in tp.print_mesh.extents],
                     **part_stats(tp.print_mesh)}

    log("Einbau-Markierungen ...")
    cup_radius = (mp.diameter / 2 + mp.fit + 1.6 + 1.5) if mp else 8.0
    helmet_out, used_mode = helmet_marks(helmet, pads, mark_mode, ring_dirs=magnet_dirs or None, ring_radius=cup_radius)

    head.export(out / "kopf.stl")
    helmet_out.export(out / "helm_angepasst.stl")
    original.export(out / "helm_original.stl")
    if viewer:
        write_viewer(out / "vorschau.html", head, helmet_out, pads)

    warnings = list(scan_warnings) + list(fit.warnings)
    if repair_state == "repariert":
        warnings.append(msg("repaired", "Das Helm-Mesh war nicht geschlossen und wurde automatisch repariert."))
    elif repair_state == "keine_wand":
        warnings.append(msg("no_wall", "Der Helm ist nur eine Fläche ohne Wandstärke – er lässt sich nicht reparieren, "
                            "gravieren oder teilen. In Blender z.B. mit „Solidify“ eine Wandstärke geben."))

    # Helm in Teile schneiden (mit Dübellöchern) und Dübel erzeugen
    split_info = {"modus": split_mode, "schnitte": [], "teile": [], "duebel": None}
    parts, dowels = [], []
    dp = dowel or DowelParams()
    if split_mode != "keine":
        cut_list = auto_cuts(helmet_out, zones, dp, bed) if split_mode == "auto" else list(cuts or [])
        split_info["schnitte"] = [{"achse": c.axis, "pos": round(c.pos, 1)} for c in cut_list]
        if split_mode == "auto" and not cut_list:
            split_info["hinweis"] = msg("fits_whole", "Der Helm passt am Stück auf das Druckbett, keine Teilung nötig.")
        if cut_list:
            log("Helm teilen und Dübellöcher setzen ...")
            parts, dowels, split_warn = split_helmet(helmet_out, cut_list, zones, dp, bed)
            parts = parts or []
            warnings += split_warn
            if parts:
                (out / "helm_teile").mkdir(exist_ok=True)
                for p in parts:
                    p.print_mesh.export(out / "helm_teile" / f"{p.name}.stl")
                    p.mesh.export(out / "helm_teile" / f"{p.name}.vorschau.stl")
                    split_info["teile"].append({"name": p.name, "datei": f"helm_teile/{p.name}.stl",
                                                "masse_mm": [round(float(v), 1) for v in p.print_mesh.extents],
                                                "passt": p.fits, "seiten": [[c.axis, s] for c, s in p.sides],
                                                **part_stats(p.print_mesh)})
                if dowels:
                    dowel_mesh(dp).export(out / "helm_teile" / "duebel_einzeln.stl")
                    dowel_set(dp, len(dowels)).export(out / "helm_teile" / f"duebel_satz_{len(dowels)}x.stl")
                    trimesh.util.concatenate([_cylinder(c, n, dp.diameter / 2, dp.length) for c, n in dowels]) \
                        .export(out / "helm_teile" / "duebel.vorschau.stl")
                split_info["duebel"] = {"anzahl": len(dowels), "durchmesser_mm": dp.diameter, "laenge_mm": dp.length,
                                        "loch_mm": round(dp.diameter + 2 * dp.clearance, 2),
                                        "satz_datei": f"helm_teile/duebel_satz_{len(dowels)}x.stl" if dowels else None}

    # Zubehör: Magnethalter, Kinnriemen-Halter, Lüfterhalter, Mikrofon-Clip
    extras = []  # (Schlüssel, Anzahl, Mesh einzeln)
    if mp and magnet_dirs:
        extras.append(("magnethalter", len(magnet_dirs), acc.magnet_cup(mp)))
    if accessories.get("riemen"):
        extras.append(("riemenhalter", 2, acc.strap_anchor(float(accessories["riemen"]))))
    if accessories.get("luefter"):
        extras.append(("luefterhalter", 1, acc.fan_mount(int(accessories["luefter"]))))
    if accessories.get("mikrofon"):
        extras.append(("mikrofonclip", 1, acc.mic_clip(float(accessories["mikrofon"]))))
    extra_info = []
    if extras:
        (out / "zubehoer").mkdir(exist_ok=True)
        for key, count, mesh in extras:
            name = f"{key}_{count}x.stl" if count > 1 else f"{key}.stl"
            acc.part_set(mesh, count).export(out / "zubehoer" / name)
            extra_info.append({"teil": key, "anzahl": count, "datei": f"zubehoer/{name}",
                               "masse_mm": [round(float(v), 1) for v in mesh.extents], **part_stats(mesh)})

    # Materialliste (TPU = Polster, PLA = Helm, Dübel, Zubehör) und 3MF je Druckplatte
    material = [{"name": p.file_stem, "gruppe": "tpu", "anzahl": 1, **part_stats(p.print_mesh)} for p in pads]
    if test_info:
        material.append({"name": "00_probestueck", "gruppe": "tpu", "anzahl": 1,
                         "volumen_cm3": test_info["volumen_cm3"], "flaeche_cm2": test_info["flaeche_cm2"]})
    pla_items = [(p.name, p.print_mesh) for p in parts] if parts else [("helm_angepasst", _bed_ready(helmet_out))]
    material += [{"name": n, "gruppe": "pla", "anzahl": 1, **part_stats(m)} for n, m in pla_items]
    if dowels:
        material.append({"name": "duebel", "gruppe": "pla_voll", "anzahl": len(dowels), **part_stats(dowel_mesh(dp))})
    material += [{"name": e["teil"], "gruppe": "pla", "anzahl": e["anzahl"], "volumen_cm3": e["volumen_cm3"],
                  "flaeche_cm2": e["flaeche_cm2"]} for e in extra_info]

    plates = {"tpu": [], "pla": []}
    if make_3mf:
        log("3MF-Dateien für den Slicer ...")
        tpu_items = [(p.file_stem, p.print_mesh) for p in pads]
        if test_info:
            tpu_items.append(("00_probestueck", trimesh.load(out / "polster" / "00_probestueck.stl")))
        pla_all = list(pla_items)
        pla_all += [(f"duebel_{i + 1}", dowel_mesh(dp)) for i in range(len(dowels))]
        for key, count, mesh in extras:
            pla_all += [(f"{key}_{i + 1}", mesh) for i in range(count)]
        plates["tpu"] = export_3mf_plates(tpu_items, bed, out / "3mf", "polster_tpu")
        plates["pla"] = export_3mf_plates(pla_all, bed, out / "3mf", "helm_pla")

    if mark_mode == "rille" and used_mode == "steg" and repair_state != "keine_wand":
        warnings.append(msg("steg_fallback", "Helm-Mesh ist nicht geschlossen – Markierungen wurden als flacher "
                            "Steg (0,8 mm) aufgesetzt statt eingraviert."))
    for pad in pads:
        s = pad.stats
        if s["zu_eng_anteil"] > 0.02:
            warnings.append(msg("pad_tight", f"{pad.zone.name}: {s['zu_eng_anteil']:.0%} der Fläche zu eng "
                                "(Polster auf Mindestdicke gekürzt).", name=pad.zone.name, pct=round(s["zu_eng_anteil"] * 100)))
        if s["ohne_helmkontakt_anteil"] > 0.05:
            warnings.append(msg("pad_float", f"{pad.zone.name}: {s['ohne_helmkontakt_anteil']:.0%} ohne Helmkontakt "
                                "(Öffnung oder > 30 mm Abstand).", name=pad.zone.name,
                                pct=round(s["ohne_helmkontakt_anteil"] * 100)))
        if not s["wasserdicht"]:
            warnings.append(msg("pad_leaky", f"{pad.zone.name}: Mesh nicht wasserdicht.", name=pad.zone.name))

    pct = [round(float(v) * 100, 1) for v in fit.scales]
    report = {
        "kopf": head_info,
        "helm": {
            "skalierung": round(fit.scale, 4),
            "skalierung_prozent": round(fit.scale * 100, 1),
            "skalierung_achsen_prozent": {"breite": pct[0], "laenge": pct[1], "hoehe": pct[2]},
            "proportional": bool(proportional and scale is None),
            "restabstand_min_mm": round(fit.min_slack, 1),
            "sitz_aus": "Ohren" if fp.ear_center is not None else "Augen" if fp.eye_point is not None else "automatisch",
            "masse_original_mm": [round(float(v), 1) for v in original_size],
            "masse_angepasst_mm": [round(float(v), 1) for v in helmet.extents],
            "markierungen": used_mode,
            "reparatur": repair_state,
        },
        "polster": {p.zone.name: {"nummer": i, "datei": f"{p.file_stem}.stl", **p.stats} for i, p in enumerate(pads, 1)},
        "befestigung": {"art": "magnet" if mp else "klett", "magnete": len(magnet_dirs) * 2 if mp else 0,
                        "magnet_mm": [mp.diameter, mp.height] if mp else None, "abstand_mm": pp.mount_gap},
        "probestueck": test_info,
        "schnittmuster": pattern_info,
        "zubehoer": extra_info,
        "material": material,
        "platten": plates,
        "teilung": split_info,
        "warnungen": texts(warnings),
        "warnungen_codes": warnings,
    }
    (out / "bericht.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return report, head, helmet_out, pads


def _bed_ready(mesh):
    """Ganzer Helm zum Drucken: Öffnung nach unten, auf das Bett gestellt."""
    m = mesh.copy()
    lo, hi = m.bounds
    m.apply_translation([-(lo[0] + hi[0]) / 2, -(lo[1] + hi[1]) / 2, -lo[2]])
    return m
