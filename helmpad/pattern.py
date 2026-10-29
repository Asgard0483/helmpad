"""2D-Schnittmuster für Schaumstoff-Polster (EVA, Polsterschaum, …) als PDF, SVG und DXF.

Jedes Polster wird flach abgewickelt (mittabstandstreue Azimutalprojektion um die Polstermitte;
bei Polstergrößen bis ~60° liegt die Längenverzerrung unter wenigen Prozent – Schaumstoff gleicht
das beim Einkleben aus). Weil Schaumstoff nur in festen Stärken erhältlich ist, wird die Dicke wie mit
Höhenlinien in Lagen zerlegt: Lage 1 ist die volle Form (kommt an den Helm), jede weitere Lage deckt
nur noch die Bereiche ab, in denen das Polster dicker ist. Alle Teile werden in Originalgröße (1:1)
ausgegeben, mit Nummer, Lage, Mittelkreuz und Pfeil, damit die Lagen deckungsgleich verklebt werden.
"""
from dataclasses import dataclass

import numpy as np

PAPER = {"A4": (210.0, 297.0), "Letter": (215.9, 279.4)}
MARGIN = 10.0     # Seitenrand mm
GAP = 6.0         # Abstand zwischen Teilen mm
LABEL = 3.5       # Schrifthöhe mm
HEADER = 64.0     # PDF Seite 1: oben Platz für Kopfzeile + 50-mm-Kontrollquadrat
HEADER_REST = 14.0  # PDF Folgeseiten: Kopfzeile + 50-mm-Messlinie
PT = 72.0 / 25.4  # PDF-Punkte je mm


@dataclass
class Piece:
    pad_nr: int
    pad_name: str
    layer: int
    layers: int
    loops: list                       # Polygone in mm (je N x 2, y nach oben), erstes = größter Umriss
    anchor: np.ndarray                # Polstermitte (Mittelkreuz + Pfeil) in Teil-Koordinaten
    x: float = 0.0                    # Lage auf dem Blatt (von layout gesetzt)
    row_top: float = 0.0
    page: int = 0

    @property
    def label(self):
        return f"{self.pad_nr}-{self.layer}/{self.layers}"

    @property
    def bounds(self):
        pts = np.vstack(self.loops)
        return pts.min(0), pts.max(0)

    def rotate90(self):
        """Um 90° drehen (Schaumstoff hat keine Laufrichtung; Pfeil und Kreuz drehen mit)."""
        r = np.array([[0.0, -1.0], [1.0, 0.0]])
        self.loops = [lp @ r.T for lp in self.loops]
        self.anchor = self.anchor @ r.T
        self.turned = not getattr(self, "turned", False)


# ---------------------------------------------------------------- Abwicklung und Lagen
def flatten(grid, frame, steps=24):
    """Rasterpunkte des Polsters -> 2D (mm), gesehen von der Kopfseite (x = Querrichtung, y = Längsrichtung).

    Richtung: Winkel in der Tangentialebene der Polstermitte. Abstand: die echte Weglänge auf der
    Mittelfläche von der Polstermitte bis zum Punkt (nicht nur Radius × Winkel – an der Stirn ändert sich
    der Abstand zur Kopfmitte stark, die Fläche ist dort länger).
    """
    dc, ea, eb = frame
    D, r = grid["D"], grid["r_mid"]
    P3 = D * r[..., None]
    nb, na = r.shape
    ic, jc = (nb - 1) / 2, (na - 1) / 2
    I, J = np.meshgrid(np.arange(nb), np.arange(na), indexing="ij")
    t = np.linspace(0, 1, steps + 1)[:, None, None]
    path_i, path_j = ic + t * (I - ic), jc + t * (J - jc)          # gerader Weg im Raster, Mitte -> Punkt
    pts = _sample(P3, np.column_stack([path_i.ravel(), path_j.ravel()])).reshape(steps + 1, nb, na, 3)
    s = np.linalg.norm(np.diff(pts, axis=0), axis=-1).sum(axis=0)
    phi = np.arctan2(D @ eb, D @ ea)
    return np.stack([s * np.cos(phi), s * np.sin(phi)], axis=-1)


def _contours(F, level):
    """Marching Squares: geschlossene Höhenlinien von F (nb x na) auf `level`, als Rasterkoordinaten (i, j).

    Der Rand wird mit -inf aufgefüllt, damit jede Linie geschlossen ist.
    """
    G = np.full((F.shape[0] + 2, F.shape[1] + 2), -np.inf)
    G[1:-1, 1:-1] = F
    inside = G >= level

    def pt(edge):
        # Punkt auf der Kante zwischen zwei Rasterpunkten, linear interpoliert (Rand: Kantenmitte)
        a, b = edge
        fa, fb = G[a], G[b]
        t = 0.5 if not (np.isfinite(fa) and np.isfinite(fb)) or fa == fb else (level - fa) / (fb - fa)
        t = min(max(t, 0.0), 1.0)
        return (a[0] + t * (b[0] - a[0]) - 1, a[1] + t * (b[1] - a[1]) - 1)

    links = {}
    nb, na = G.shape
    for i in range(nb - 1):
        for j in range(na - 1):
            corners = [(i, j), (i, j + 1), (i + 1, j + 1), (i + 1, j)]
            c = [inside[x] for x in corners]
            if all(c) or not any(c):
                continue
            keys = [tuple(sorted((corners[k], corners[(k + 1) % 4]))) for k in range(4) if c[k] != c[(k + 1) % 4]]
            if len(keys) == 2:
                pairs = [(keys[0], keys[1])]
            else:  # Sattelpunkt: der Mittelwert entscheidet, wie verbunden wird
                vals = [G[x] for x in corners if np.isfinite(G[x])]
                centre_in = bool(vals) and np.mean(vals) >= level
                pairs = [(keys[0], keys[1]), (keys[2], keys[3])] if centre_in == c[0] else [(keys[0], keys[3]), (keys[1], keys[2])]
            for a, b in pairs:
                links.setdefault(a, []).append(b)
                links.setdefault(b, []).append(a)

    loops, used = [], set()
    for start in links:
        if start in used:
            continue
        loop, prev, cur = [start], None, start
        used.add(start)
        while True:
            nxt = [n for n in links[cur] if n != prev and n not in used]
            if not nxt:
                break
            prev, cur = cur, nxt[0]
            used.add(cur)
            loop.append(cur)
        if len(loop) >= 3:
            loops.append(np.array([pt(e) for e in loop]))
    return loops


def _sample(P2, ij):
    """2D-Koordinaten an gebrochenen Rasterpositionen (bilinear)."""
    nb, na = P2.shape[:2]
    i = np.clip(ij[:, 0], 0, nb - 1)
    j = np.clip(ij[:, 1], 0, na - 1)
    i0, j0 = np.minimum(i.astype(int), nb - 2), np.minimum(j.astype(int), na - 2)
    ti, tj = (i - i0)[:, None], (j - j0)[:, None]
    return ((P2[i0, j0] * (1 - tj) + P2[i0, j0 + 1] * tj) * (1 - ti)
            + (P2[i0 + 1, j0] * (1 - tj) + P2[i0 + 1, j0 + 1] * tj) * ti)


def _simplify(poly, tol=0.15):
    """Ramer-Douglas-Peucker für geschlossene Linien (kleinere Dateien, Abweichung < tol mm)."""
    def rdp(pts):
        if len(pts) < 3:
            return pts
        a, b = pts[0], pts[-1]
        ab = b - a
        n = np.linalg.norm(ab)
        d = np.abs(ab[0] * (pts[:, 1] - a[1]) - ab[1] * (pts[:, 0] - a[0])) / n if n > 1e-9 else np.linalg.norm(pts - a, axis=1)
        k = int(np.argmax(d))
        if d[k] <= tol:
            return np.array([a, b])
        return np.vstack([rdp(pts[:k + 1])[:-1], rdp(pts[k:])])

    far = int(np.argmax(np.linalg.norm(poly - poly[0], axis=1)))
    return np.vstack([rdp(poly[:far + 1])[:-1], rdp(np.vstack([poly[far:], poly[:1]]))[:-1]])


def _area(poly):
    x, y = poly[:, 0], poly[:, 1]
    return 0.5 * abs(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))


def pad_pieces(pad, nr, sheet):
    """Alle Lagen eines Polsters als Schnittteile (Lage 1 = volle Form an der Helmseite)."""
    g = pad.grid
    P2 = flatten(g, pad.zone.frame())
    F = g["thick"]
    n_layers = max(1, int(np.ceil((float(F.max()) - sheet / 2) / sheet)))
    centre = _sample(P2, np.array([[(F.shape[0] - 1) / 2, (F.shape[1] - 1) / 2]]))[0]
    pieces = []
    for k in range(1, n_layers + 1):
        level = -1e9 if k == 1 else (k - 0.5) * sheet  # Lage 1 ist immer der ganze Umriss
        loops = [_simplify(_sample(P2, ij)) for ij in _contours(F, level)]
        loops = [lp for lp in loops if _area(lp) > 40.0]  # Krümel unter 40 mm² weglassen
        if not loops:
            continue
        loops.sort(key=lambda lp: -_area(lp))
        pieces.append(Piece(nr, pad.zone.name, k, n_layers, loops, anchor=centre))
    for p in pieces:
        p.layers = len(pieces)
    return pieces


# ---------------------------------------------------------------- Anordnung (gemeinsam für alle Formate)
def layout(pieces, width, height=None, first_height=None):
    """Teile zeilenweise anordnen. Mit `height` werden Seiten gefüllt (PDF), sonst ein langes Blatt (SVG/DXF).

    Speichert je Teil Spalte `x`, Zeilenoberkante `row_top` (mm von oben) und Seite; gibt (Seiten, Gesamthöhe) zurück.
    `first_height`: nutzbare Höhe der ersten Seite, falls abweichend (dort steht das Kontrollquadrat).
    """
    page_h = lambda pg: first_height if (pg == 0 and first_height is not None) else height  # noqa: E731

    def size(p):
        lo, hi = p.bounds
        return hi - lo

    # Teile, die breiter als das Blatt sind, aber hochkant passen, gleich drehen
    for p in pieces:
        w, h = size(p)
        if w > width and h <= width:
            p.rotate90()
    todo = sorted(pieces, key=lambda p: -size(p)[1])
    page, y = 0, 0.0
    while todo:
        first = todo.pop(0)  # das höchste verbleibende Teil eröffnet die Zeile und legt ihre Höhe fest
        row_h = size(first)[1]
        if height is not None and y > 0 and y + row_h > page_h(page):
            page, y = page + 1, 0.0
        first.x, first.row_top, first.page = 0.0, y, page
        x = size(first)[0] + GAP
        # Lücke rechts mit den größten noch passenden Teilen füllen (auch um 90° gedreht)
        filled = True
        while filled:
            filled = False
            for p in sorted(todo, key=lambda p: -np.prod(size(p))):
                for turn in (False, True):
                    w, h = size(p)[::-1] if turn else size(p)
                    if x + w <= width and h <= row_h + 1e-6:
                        if turn:
                            p.rotate90()
                        p.x, p.row_top, p.page = x, y, page
                        x += w + GAP
                        todo.remove(p)
                        filled = True
                        break
                if filled:
                    break
        y += row_h + GAP
    return page + 1, y - GAP


def _place(p, pts):
    """Teil-Koordinaten (y nach oben) -> Blatt-Koordinaten (x nach rechts, y nach unten ab Blattoberkante)."""
    lo, hi = p.bounds
    return np.column_stack([p.x + pts[:, 0] - lo[0], p.row_top + hi[1] - pts[:, 1]])


def _marks(p):
    """Mittelkreuz + Pfeil (zeigt in Längsrichtung) als Linienzüge, in Teil-Koordinaten."""
    s = 4.0
    shapes = [np.array([[-s, 0], [s, 0]]), np.array([[0, -s], [0, 3 * s]]),
              np.array([[-1.5, 2 * s], [0, 3 * s], [1.5, 2 * s]])]
    if getattr(p, "turned", False):  # gedrehtes Teil: Pfeil zeigt weiter in die Längsrichtung des Polsters
        r = np.array([[0.0, -1.0], [1.0, 0.0]])
        shapes = [sh @ r.T for sh in shapes]
    return [p.anchor + sh for sh in shapes]


def _label_pos(p):
    return _place(p, np.array([p.anchor + [5.5, -1.2]]))[0]


# ---------------------------------------------------------------- SVG (1:1, ein Blatt, z.B. für Schneideplotter)
def write_svg(path, pieces, width=300.0):
    _, h = layout(pieces, width)
    W, H = width + 2 * MARGIN, h + 2 * MARGIN

    def d(q, close):
        return "M" + " L".join(f"{x + MARGIN:.2f},{y + MARGIN:.2f}" for x, y in q) + (" Z" if close else "")

    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W:.1f}mm" height="{H:.1f}mm" viewBox="0 0 {W:.1f} {H:.1f}">',
           '<style>.cut{fill:none;stroke:#000;stroke-width:0.3}.mark{fill:none;stroke:#e4572e;stroke-width:0.25}'
           f'text{{font-family:Arial,sans-serif;font-size:{LABEL}px;fill:#333}}</style>',
           '<rect width="100%" height="100%" fill="#fff"/>']
    for p in pieces:
        out.append(f'<g id="teil_{p.pad_nr}_{p.layer}">')
        out += [f'<path class="cut" d="{d(_place(p, lp), True)}"/>' for lp in p.loops]
        out += [f'<path class="mark" d="{d(_place(p, m), False)}"/>' for m in _marks(p)]
        lx, ly = _label_pos(p) + MARGIN
        out.append(f'<text x="{lx:.2f}" y="{ly:.2f}">{p.label}</text>')
        out.append(f'<text x="{lx:.2f}" y="{ly + LABEL + 0.6:.2f}" font-size="{LABEL * 0.75:.2f}">{p.pad_name}</text>')
        out.append("</g>")
    out.append("</svg>")
    path.write_text("\n".join(out), encoding="utf-8")


# ---------------------------------------------------------------- DXF (R12, mm, z.B. für Lasercutter)
def write_dxf(path, pieces, width=300.0):
    layout(pieces, width)
    lines = ["0", "SECTION", "2", "HEADER", "9", "$INSUNITS", "70", "4", "0", "ENDSEC",
             "0", "SECTION", "2", "ENTITIES"]

    def poly(q, layer, closed):  # DXF: y nach oben -> Blattkoordinaten spiegeln
        lines.extend(["0", "POLYLINE", "8", layer, "66", "1", "70", "1" if closed else "0"])
        for x, y in q:
            lines.extend(["0", "VERTEX", "8", layer, "10", f"{x:.3f}", "20", f"{-y:.3f}", "30", "0.0"])
        lines.extend(["0", "SEQEND", "8", layer])

    for p in pieces:
        for lp in p.loops:
            poly(_place(p, lp), "SCHNITT", True)
        for m in _marks(p):
            poly(_place(p, m), "MARKIERUNG", False)
        x, y = _label_pos(p)
        lines.extend(["0", "TEXT", "8", "BESCHRIFTUNG", "10", f"{x:.3f}", "20", f"{-y:.3f}", "30", "0.0",
                      "40", f"{LABEL}", "1", f"{p.label} {p.pad_name}"])
    lines.extend(["0", "ENDSEC", "0", "EOF"])
    path.write_text("\n".join(lines) + "\n", encoding="ascii", errors="replace")


# ---------------------------------------------------------------- PDF (1:1, mehrseitig, ohne Zusatzbibliothek)
def _pdf_text(s):
    s = s.encode("cp1252", "replace").decode("latin-1")
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def write_pdf(path, pieces, paper="A4", title="HelmPad"):
    """Mehrseitiges PDF in Originalgröße. Jede Seite trägt ein 50-mm-Kontrollquadrat zum Prüfen des Maßstabs."""
    pw, ph = PAPER.get(paper, PAPER["A4"])
    usable_w = pw - 2 * MARGIN
    pages, _ = layout(pieces, usable_w, ph - 2 * MARGIN - HEADER_REST, first_height=ph - 2 * MARGIN - HEADER)
    area_top = ph - MARGIN - HEADER  # Oberkante des Bereichs für die Teile (PDF: y nach oben), je Seite gesetzt

    def stroke(q, close):
        q = np.column_stack([(MARGIN + q[:, 0]) * PT, (area_top - q[:, 1]) * PT])
        return " ".join([f"{q[0, 0]:.2f} {q[0, 1]:.2f} m"] + [f"{x:.2f} {y:.2f} l" for x, y in q[1:]]) + (" h S" if close else " S")

    def text(x, y, size_pt, s):  # x, y in mm (PDF-Koordinaten, y nach oben)
        return f"BT /F1 {size_pt:.1f} Tf {x * PT:.2f} {y * PT:.2f} Td ({_pdf_text(s)}) Tj ET"

    streams = []
    for pg in range(pages):
        top = ph - MARGIN
        area_top = top - (HEADER if pg == 0 else HEADER_REST)
        cmd = ["0 0 0 RG 0 0 0 rg", text(MARGIN, top - 4, 9, f"{title}  {pg + 1}/{pages}  1:1")]
        if pg == 0:  # Kontrollquadrat: ausdrucken, nachmessen – 50 mm heißt, der Druckmaßstab stimmt
            sx, sy = pw - MARGIN - 50, top - 58
            cmd += ["0.5 w", f"{sx * PT:.2f} {sy * PT:.2f} {50 * PT:.2f} {50 * PT:.2f} re S", text(sx + 17, sy + 23, 8, "50 mm")]
        else:  # Messlinie 50 mm mit Endstrichen
            lx, ly = pw - MARGIN - 50, top - 5
            cmd += ["0.5 w", f"{lx * PT:.2f} {ly * PT:.2f} m {(lx + 50) * PT:.2f} {ly * PT:.2f} l S",
                    f"{lx * PT:.2f} {(ly - 1.5) * PT:.2f} m {lx * PT:.2f} {(ly + 1.5) * PT:.2f} l S",
                    f"{(lx + 50) * PT:.2f} {(ly - 1.5) * PT:.2f} m {(lx + 50) * PT:.2f} {(ly + 1.5) * PT:.2f} l S",
                    text(lx + 19, ly - 5, 7, "50 mm")]
        for p in [q for q in pieces if q.page == pg]:
            cmd.append("0 0 0 RG 0.85 w")  # 0.3 mm
            cmd += [stroke(_place(p, lp), True) for lp in p.loops]
            cmd.append("0.894 0.341 0.18 RG 0.7 w")
            cmd += [stroke(_place(p, m), False) for m in _marks(p)]
            lx, ly = _label_pos(p)
            cmd.append(text(MARGIN + lx, area_top - ly, LABEL * PT, p.label))
            cmd.append(text(MARGIN + lx, area_top - ly - LABEL - 0.6, LABEL * 0.75 * PT, p.pad_name))
        streams.append("\n".join(cmd).encode("latin-1"))

    objs = [b"<< /Type /Catalog /Pages 2 0 R >>"]
    page_ids = [3 + 2 * i for i in range(pages)]
    font_id = 3 + 2 * pages
    objs.append(f"<< /Type /Pages /Kids [{' '.join(f'{i} 0 R' for i in page_ids)}] /Count {pages} >>".encode())
    for i, st in enumerate(streams):
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {pw * PT:.2f} {ph * PT:.2f}] "
                    f"/Resources << /Font << /F1 {font_id} 0 R >> >> /Contents {page_ids[i] + 1} 0 R >>".encode())
        objs.append(f"<< /Length {len(st)} >>\nstream\n".encode() + st + b"\nendstream")
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")

    data = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for i, o in enumerate(objs, 1):
        offsets.append(len(data))
        data += f"{i} 0 obj\n".encode() + o + b"\nendobj\n"
    xref = len(data)
    data += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    data += "".join(f"{off:010d} 00000 n \n" for off in offsets).encode()
    data += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    path.write_bytes(bytes(data))
    return pages


def write_pattern(out_dir, pads, sheet=6.0, paper="A4"):
    """Schnittmuster aller Polster als PDF, SVG und DXF. Gibt eine Zusammenfassung für den Bericht zurück."""
    out_dir.mkdir(parents=True, exist_ok=True)
    pieces, summary = [], []
    for nr, pad in enumerate(pads, 1):
        if pad.grid is None:
            continue
        pp = pad_pieces(pad, nr, sheet)
        pieces += pp
        summary.append({"nummer": nr, "name": pad.zone.name, "lagen": len(pp)})
    if not pieces:
        return None
    pages = write_pdf(out_dir / "schnittmuster.pdf", pieces, paper)
    write_svg(out_dir / "schnittmuster.svg", pieces)  # ordnet die Teile für ein durchgehendes Blatt neu an
    write_dxf(out_dir / "schnittmuster.dxf", pieces)
    return {"staerke_mm": sheet, "papier": paper, "seiten": pages, "teile": len(pieces), "polster": summary,
            "dateien": {"pdf": "schnittmuster/schnittmuster.pdf", "svg": "schnittmuster/schnittmuster.svg",
                        "dxf": "schnittmuster/schnittmuster.dxf"}}
