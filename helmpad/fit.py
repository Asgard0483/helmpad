"""Kopf im Helm platzieren und Helmgröße bestimmen.

Idee: Von einem Punkt im Helm-Hohlraum aus wird die Innenseite abgetastet. Für jede
mögliche Kopfposition c lässt sich dann direkt ausrechnen, um welchen Faktor der
Helm (um c herum) skaliert werden muss, damit überall genug Abstand zum Kopf bleibt.
Gesucht wird die Position mit dem kleinsten nötigen Faktor = der Helm sitzt am
"natürlichsten" auf dem Kopf.
"""
from dataclasses import dataclass, field

import numpy as np

from .messages import msg
from .zones import glasses_band
from .rays import RadialTable, az_el_from_dirs, fibonacci_sphere, first_hits


@dataclass
class FitParams:
    gap_pad: float              # nötiger Abstand Kopf -> Helm in den Polsterzonen
    gap_other: float = 6.0      # Mindestabstand außerhalb der Polster (Ohren, Schläfen)
    gap_face: float = 25.0      # vorne unten: Platz für Nase/Gesicht
    min_check_el: float = -20.0 # tiefer wird nicht geprüft (Kinnbereich ist zu ungenau modelliert)
    scale: float | None = None  # None = automatisch, sonst fester Faktor
    offset: tuple = (0.0, 0.0, 0.0)  # manuelle Korrektur der Kopfposition (mm, nach Skalierung)
    ear_center: np.ndarray | None = None  # markierte Ohr-Mitte (Helm-Koordinaten) -> Kopf-Ursprung
    eye_point: np.ndarray | None = None   # markierte Augenmitte (Helm-Koordinaten) -> Sitzhöhe
    eye_up: float = 10.0                  # Augenhöhe über dem Ohrkanal am Kopfmodell
    proportional: bool = False            # Breite/Länge/Höhe einzeln an die Kopfform anpassen
    max_distort: float = 0.12             # so weit dürfen die Achsen höchstens vom gleichmäßigen Faktor abweichen
    glasses: bool = False                 # zusätzlicher Platz für Brillenbügel (Schläfe bis Ohr)
    glasses_gap: float = 5.0


@dataclass
class FitResult:
    center: np.ndarray   # Kopf-Ursprung in Helm-Koordinaten (vor Skalierung)
    scale: float         # gleichmäßiger Faktor
    scales: np.ndarray   # tatsächliche Faktoren je Achse (X Breite, Y Länge, Z Höhe)
    min_slack: float     # kleinster Restabstand (mm) über den Anforderungen, nach Skalierung
    warnings: list = field(default_factory=list)

    def transform(self):
        """4x4-Matrix: Helm-Koordinaten -> Kopf-Koordinaten (inkl. Skalierung)."""
        m = np.diag([*self.scales, 1.0])
        m[:3, 3] = -self.scales * self.center
        return m

    def unscaled_transform(self):
        """Nur verschieben (Originalgröße), für den Vorher/Nachher-Vergleich."""
        m = np.eye(4)
        m[:3, 3] = -self.center
        return m


def _inner_points(helmet, guess, n=8000):
    dirs = fibonacci_sphere(n)
    dist = first_hits(helmet, guess, dirs)
    ok = np.isfinite(dist)
    return guess + dirs[ok] * dist[ok, None], ok.mean()


def _requirements(dirs, zones, p: FitParams):
    az, el = az_el_from_dirs(dirs)
    need = np.full(len(dirs), p.gap_other)
    face = (np.abs(az) < 45) & (el < 10)
    need[face] = p.gap_face
    if p.glasses:
        need = need + p.glasses_gap * (glasses_band(dirs) > 0.5)
    in_pad = np.zeros(len(dirs), bool)
    for z in zones:
        in_pad |= z.contains(dirs)
    need[in_pad] = p.gap_pad
    return need, el >= p.min_check_el, in_pad


def _evaluate(points, c, head_r, zones, p):
    q = points - c
    rq = np.linalg.norm(q, axis=1)
    d = q / rq[:, None]
    need, valid, _ = _requirements(d, zones, p)
    r_h = head_r(d)
    if p.scale is None:
        s = np.max((r_h[valid] + need[valid]) / rq[valid])
    else:
        s = p.scale
    slack = np.min(s * rq[valid] - r_h[valid] - need[valid])
    return s, slack


def _slack_axes(points, c, scales, head_r, zones, p):
    """Kleinster Restabstand bei Skalierung je Achse um c."""
    q = (points - c) * scales
    rq = np.linalg.norm(q, axis=1)
    d = q / rq[:, None]
    need, valid, _ = _requirements(d, zones, p)
    return np.min(rq[valid] - head_r(d[valid]) - need[valid])


def _axes_cost(q, scales, head_r, zones, p, cap):
    """(kleinster Restabstand, mittlerer Polsterspalt) für Skalierung je Achse.

    Der Spalt ist bei `cap` gedeckelt: Wo der Helm ohnehin weit weg ist (Öffnung, > 30 mm),
    soll eine winzige Verbesserung nicht die übrigen Achsen aufblähen.
    """
    qs = q * scales
    rq = np.linalg.norm(qs, axis=1)
    d = qs / rq[:, None]
    need, valid, in_pad = _requirements(d, zones, p)
    gap = rq - head_r(d)
    slack = float(np.min(gap[valid] - need[valid]))
    pad = valid & in_pad
    cost = float(np.mean(np.minimum(gap[pad], cap))) if pad.any() else 0.0
    return slack, cost


def _fit_axes(points, c, s, head_r, zones, p, steps=7, rounds=3):
    """Faktoren je Achse so wählen, dass die Polster insgesamt möglichst dünn und gleichmäßig werden.

    Rastersuche über Breite/Länge/Höhe (je höchstens `max_distort` vom gleichmäßigen Faktor),
    in mehreren Runden immer feiner. Erlaubt sind nur Faktoren, bei denen überall genug Abstand
    zum Kopf bleibt; bei Gleichstand gewinnt die geringere Verzerrung. Findet die Suche nichts
    Besseres, bleibt es beim gleichmäßigen Faktor.
    """
    q = points - c
    if len(q) > 3000:  # für die Suche reicht eine Stichprobe, geprüft wird am Ende mit allen Punkten
        q = q[np.random.default_rng(0).choice(len(q), 3000, replace=False)]
    cap = p.gap_pad + 20.0
    lo, hi = s * (1 - p.max_distort), s * (1 + p.max_distort)

    def score(sc):
        slack, cost = _axes_cost(q, sc, head_r, zones, p, cap)
        return slack, cost + 2.0 * float(np.abs(sc / s - 1).sum())  # leichte Strafe für Verzerrung

    best = np.full(3, s)
    best_score = score(best)[1]
    center, span = best.copy(), p.max_distort
    for _ in range(rounds):
        f = np.linspace(-span, span, steps)
        for fx in f:
            for fy in f:
                for fz in f:
                    sc = np.clip(center + s * np.array([fx, fy, fz]), lo, hi)
                    slack, sco = score(sc)
                    if slack >= 0 and sco < best_score - 1e-9:
                        best, best_score = sc, sco
        center, span = best.copy(), span / 3
    if _slack_axes(points, c, best, head_r, zones, p) < 0:  # Stichprobe war zu optimistisch
        return np.full(3, s)
    return best


def _inner_center_x(helmet, guess, ext):
    """Seitliche Mitte des Hohlraums: Mitte zwischen linker und rechter Innenwand (mehrere Höhen).

    Die Bounding-Box taugt dafür nicht: Ein Anbau auf einer Seite (Antenne, Horn) verschiebt sie,
    der Kopf säße dann schief und der Helm würde unnötig groß.
    """
    from .marks import first_hits_from
    zs = guess[2] + np.array([-0.15, -0.05, 0.05, 0.15]) * ext[2]
    ys = guess[1] + np.array([-0.1, 0.0, 0.1]) * ext[1]
    o = np.array([[guess[0], y, z] for z in zs for y in ys])
    right = first_hits_from(helmet, o, np.tile([1.0, 0, 0], (len(o), 1)))
    left = first_hits_from(helmet, o, np.tile([-1.0, 0, 0], (len(o), 1)))
    mids = guess[0] + (right - left) / 2
    ok = np.isfinite(mids) & (right + left > 0.3 * ext[0])  # beide Wände getroffen, kein Hohlraum-Splitter
    return float(np.median(mids[ok])) if ok.sum() >= 3 else float(guess[0])


def fit_head(helmet, head_mesh, zones, p: FitParams) -> FitResult:
    head_r = RadialTable(head_mesh)
    lo, hi = helmet.bounds
    ext = hi - lo
    if p.ear_center is None:
        guess = (lo + hi) / 2
        guess[0] = _inner_center_x(helmet, guess, ext)
    else:
        guess = np.asarray(p.ear_center, float)
    points, hit_ratio = _inner_points(helmet, guess)
    warnings = []
    if hit_ratio < 0.4:
        warnings.append(msg("low_hits", f"Nur {hit_ratio:.0%} der Prüfstrahlen treffen die Helm-Innenseite - "
                            "ist die Ausrichtung (oben/vorne) richtig?", pct=round(hit_ratio * 100)))

    # Bewertung: automatisch -> kleinster Faktor gewinnt; fester Faktor -> größter Restabstand
    def score(c):
        s, slack = _evaluate(points, c, head_r, zones, p)
        return s if p.scale is None else -slack

    if p.ear_center is not None:
        # Ohren markiert: Kopf-Ursprung liegt genau dazwischen
        best = guess.copy()
    elif p.eye_point is not None:
        # Augen markiert: nur die Höhe steht fest (hängt von der Skalierung ab -> iterieren).
        # Seitlich bleibt der Kopf in der Helmmitte, vor/zurück wird gesucht.
        eye = np.asarray(p.eye_point, float)
        s = p.scale or 1.0
        best = np.array([guess[0], guess[1], eye[2]])
        for _ in range(4):
            z = eye[2] - p.eye_up / s
            for step, ry in ((3.0, 0.25 * ext[1]), (0.5, 3.0)):
                cands = [np.array([guess[0], best[1] + dy, z]) for dy in np.arange(-ry, ry + 1e-9, step)]
                best = cands[int(np.argmin([score(c) for c in cands]))]
            s, _ = _evaluate(points, best, head_r, zones, p)
    else:
        # grob über einen großen Bereich, dann fein um das beste Ergebnis (x bleibt symmetrisch)
        best = guess
        for step, ry, rz in ((4.0, 0.25 * ext[1], 0.30 * ext[2]), (1.0, 4.0, 4.0)):
            cands = [best + np.array([0.0, dy, dz])
                     for dy in np.arange(-ry, ry + 1e-9, step)
                     for dz in np.arange(-rz, rz + 1e-9, step)]
            best = cands[int(np.argmin([score(c) for c in cands]))]

    s, slack = _evaluate(points, best, head_r, zones, p)
    if any(p.offset):
        best = best + np.asarray(p.offset, float) / s
        s, slack = _evaluate(points, best, head_r, zones, p)

    scales = np.full(3, s)
    if p.proportional and p.scale is None:
        scales = _fit_axes(points, best, s, head_r, zones, p)
        slack = _slack_axes(points, best, scales, head_r, zones, p)

    if s < 0.7 or s > 1.4:
        warnings.append(msg("odd_scale", f"Ungewöhnlicher Skalierungsfaktor {s:.2f} - Einheit (mm/cm/m) oder "
                            "Ausrichtung prüfen.", s=round(float(s), 2)))
    if slack < -0.5:
        warnings.append(msg("too_tight", f"Helm ist bei fester Skalierung zu eng: an der engsten Stelle fehlen "
                            f"{-slack:.1f} mm.", mm=round(float(-slack), 1)))
    return FitResult(center=best, scale=float(s), scales=scales, min_slack=float(slack), warnings=warnings)
