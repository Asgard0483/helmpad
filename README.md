# HelmPad

Erzeugt aus einer Helm-STL und Kopfmaßen druckbare, mehrteilige Polster und skaliert den Helm passend zum Kopf.

## Einrichtung (einmalig)

```bash
py -3.13 -m venv %LOCALAPPDATA%\helmpad-venv
%LOCALAPPDATA%\helmpad-venv\Scripts\python -m pip install -r requirements.txt
```

Die Umgebung liegt bewusst unter einem kurzen Pfad, weil Windows sonst einige Bibliotheken (Embree, rtree) nicht laden kann.

## Oberfläche (empfohlen)

Doppelklick auf **`HelmPad starten.bat`**. Beim ersten Mal wird die Python-Umgebung eingerichtet, danach öffnet sich der Browser mit http://127.0.0.1:8770:

Jeder Schritt hat einen eigenen Reiter (oben in der Seitenleiste, mit Zurück/Weiter unten). Über „Anleitungen“ oder das **?** je Seite lässt sich eine kurze Schritt-für-Schritt-Anleitung einblenden.

1. **Helm**: STL hineinziehen. HelmPad erkennt die Lage selbst (Halsöffnung = unten, Sehschlitz/Gesichtsfeld = vorne,
   Spiegelsymmetrie = links/rechts) und meldet, wie sicher es ist; „Vorne/hinten tauschen“ und „Rückgängig“ helfen,
   wenn es nicht passt. Bei offenem Gesicht (Gladiator, Wikinger) oder geschlossenem Visier ist die Neigung bzw.
   vorne/hinten nur geschätzt – dann am besten Augen und Scheitel markieren (Schritt 2).
   Von Hand ausrichten, bis das Visier zum grünen Pfeil (VORNE) zeigt:
   Vorlagen „Z oben“ / „Y oben“, 90°-Knöpfe oder frei mit der Maus an den Ringen drehen (5°-Raster abschaltbar)
2. **Punkte** (optional) (Augen, Scheitel, Kinn, Ohr links/rechts): Punkt wählen, ins Modell klicken.
     „Automatisch ausrichten“ dreht den Helm danach selbst (braucht beide Ohren + einen weiteren Punkt,
     oder Scheitel + Kinn + Augen). Die Ohren legen außerdem die Sitzposition des Kopfes fest, sonst die Augen die Höhe.
3. **Kopf erfassen**: Maße eingeben (iPhone-Scan und Fotos folgen in Phase 2/3)
4. **Aufteilung**: Vorlage mit 3, 6 oder 10 Teilen, jede Zone verschieben, vergrößern, drehen, quer oder längs teilen
5. **Polster & Sitz**: Dicke, Anpressdruck, Klettband, Lüftung, Sitzkorrektur
   - Helmgröße: gleichmäßig (Form bleibt exakt), an Kopfform (Breite/Länge/Höhe einzeln, max. ±12 %) oder fester Faktor
   - Einbauhilfe: Rahmen + Nummer jedes Polsters in die Helm-Innenseite (Rille oder Steg), Nummer auf jede Polster-Rückseite
   - Befestigung: Klettband oder **Magnete** (Taschen in den Polstern + Magnethalter zum Einkleben; Position im Helm graviert)
   - **Brillen-Freiraum**: Rinne in den Polstern und mehr Platz im Helm entlang der Brillenbügel
   - **Probestück**: kleines Polster zum Testen von TPU-Härte und Anpressdruck
   - **Zubehör**: Kinnriemen-Halter, Lüfterhalter (25/30/40 mm), Mikrofon-Clip – zum Einkleben
   - **Schaumstoff statt 3D-Druck**: 2D-Schnittmuster aller Polster als **PDF** (1:1 auf A4/Letter, mit
     50-mm-Kontrollquadrat), **SVG** (Schneideplotter wie Cricut/Silhouette) und **DXF** (Laser/CAD). Dicke Polster
     werden wie Höhenlinien in Lagen der gewählten Schaumstoffstärke zerlegt; Beschriftung „Polster-Lage/Lagen“,
     Mittelkreuz und Pfeil zum deckungsgleichen Verkleben (Lage 1 an den Helm)
6. **Helm teilen** (optional): automatisch passend zum Bauraum deines Druckers oder eigene Schnitte
   (links/rechts, vorne/hinten, waagerecht). An den Schnitten entstehen Löcher für Verbindungsdübel, die Dübel
   gibt es als eigene STL. Dünne Wände bekommen an den Dübelstellen innen einen Verstärkungsblock.
7. **Berechnen**: 3D-Ergebnis mit Schnitt-, Einbau- und Explosionsansicht, Vergleich mit der Originalgröße,
   Download pro Teil oder alles als ZIP. Dazu **3MF-Dateien je Druckplatte** (z.B. für Bambu Studio, getrennt nach
   TPU und PLA) und eine **Schätzung von Material, Druckzeit und Kosten** (Preise und Füllung einstellbar)

**Projekt speichern/laden** (💾/📂 oben): sichert alle Eingaben, Ausrichtung, Punkte und Polsterzonen als JSON-Datei.
Die Helm-Datei selbst wird nicht mitgespeichert – nach dem Laden einfach dieselbe STL wieder hineinziehen.

Offene (nicht geschlossene) Helm-STLs werden beim Berechnen automatisch mit MeshFix repariert.

Hochgeladene Helme und Ergebnisse landen in `arbeitsdaten/` und können jederzeit gelöscht werden.

### Sprachen

Oben rechts lässt sich die Sprache umschalten: Deutsch, English, Español, Français, Italiano, Hrvatski.
Alle Texte stehen in `helmpad/web/i18n.js`. Eine neue Sprache = Eintrag in `LANGS` + ein Block mit den
Übersetzungen (fehlende Texte fallen auf Deutsch zurück). Polsternamen der Vorlagen werden mit übersetzt,
dadurch heißen auch die Dateien z.B. `01_forehead.stl`.

### Spenden (PayPal / Revolut)

Der Knopf „♥ Unterstützen“ öffnet ein Fenster mit PayPal und Revolut. Die Links stehen in
`helmpad/web/spenden.json` und sind noch leer, die Knöpfe zeigen dann „bald verfügbar“. Zum Freischalten eintragen:

```json
{
  "paypal": "https://paypal.me/DEINNAME",
  "revolut": "https://revolut.me/DEINNAME"
}
```

Nur `https://`-Links werden angenommen; ein Neustart ist nicht nötig, einmal die Seite neu laden reicht.

## Kommandozeile

```bash
%LOCALAPPDATA%\helmpad-venv\Scripts\python -m helmpad --helm mein_helm.stl --umfang 570 --out ausgabe
```

Wichtige Optionen (alle in mm):

| Option | Bedeutung |
|---|---|
| `--umfang` | Kopfumfang über Stirn/Augenbrauen und Hinterkopf (Pflicht) |
| `--laenge`, `--breite`, `--ohr-scheitel`, `--gesicht` | optionale weitere Maße, verbessern das Kopfmodell |
| `--oben`, `--vorne` | Ausrichtung der STL (Standard `+z` / `-y` wie Blender) |
| `--einheit` | `mm`, `cm`, `m`, `in` |
| `--skalierung` | `auto` (Helm wird angepasst) oder fester Faktor, z.B. `1.0` |
| `--versatz X Y Z` | Kopf im Helm verschieben, z.B. `0 0 -5` = Helm sitzt 5 mm höher |
| `--dicke`, `--kompression`, `--klett` | Polsterdicke, Anpressdruck, Platz fürs Klettband |
| `--kanaele` | Abstand der Lüftungskanäle, `0` = keine |
| `--zonen` | eigene Polsterzonen als JSON (Vorlage: `ausgabe/zonen.json`) |
| `--proportional` | Breite/Länge/Höhe des Helms einzeln an die Kopfform anpassen |
| `--markierung` | Einbau-Markierungen im Helm: `rille` (Standard), `steg`, `keine` |
| `--teilen auto` | Helm so oft teilen, dass jedes Teil in `--bauraum X Y Z` passt (Standard 256³) |
| `--schnitt ACHSE POS` | eigener Schnitt, mehrfach möglich: `x` links/rechts, `y` vorne/hinten, `z` oben/unten |
| `--schaum MM`, `--papier` | 2D-Schnittmuster für Schaumstoff dieser Stärke (PDF/SVG/DXF), Papier A4 oder Letter |
| `--duebel D L` | Dübel-Durchmesser und -Länge (Standard 5 × 16 mm) |

## Ausgabe

- `polster/01_….stl` – ein Teil pro Datei, nummeriert wie die Markierungen im Helm, Helmseite nach unten
- `helm_angepasst.stl` – skalierter Helm mit Einbau-Markierungen
- `helm_original.stl` – Helm in Originalgröße an derselben Stelle (zum Vergleich)
- `helm_teile/` – bei Teilung: Helm-Teile (Schnittfläche unten), `duebel_einzeln.stl`, `duebel_satz_Nx.stl`
- `kopf.stl` – erzeugtes Kopfmodell
- `vorschau.html` – 3D-Vorschau (über einen lokalen Webserver öffnen)
- `bericht.json` – Skalierung, Polsterdicken, Warnungen

Drucktipp: TPU 85A–95A, 8–15 % Gyroid-Füllung, Stützstrukturen (Tree) für die gewölbte Unterseite.

## Testen

```bash
%LOCALAPPDATA%\helmpad-venv\Scripts\python tools/make_test_helmet.py
```
