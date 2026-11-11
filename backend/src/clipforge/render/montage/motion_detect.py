"""Detection des pics audio qui alimentent les fenetres de punch-in de camera.

Deplace tel quel depuis l'ancien render/punch.py (Task 5) : semantique de
detection inchangee, seul l'emplacement bouge. legacy_zoom_expression est le
repli V5 (montage=0) que render/montage_plan.py choisit a la place de l'engin
organique de motion.py, qui reexporte les trois noms de ce module pour garder
un point d'import unique cote consommateurs.
"""
import statistics
from dataclasses import dataclass

MIN_PROMINENCE = 0.1


@dataclass(frozen=True)
class PunchWindow:
    start: float
    end: float


def detect_punches(series, *, clip_start, clip_end, max_count=3, min_gap_s=6.0,
                   window_s=1.4, threshold_pct=80.0):
    """Detect punch-in windows from audio peak scores.

    Args:
        series: List of (time_s, score) tuples in absolute VOD seconds.
        clip_start: Clip start time in absolute VOD seconds.
        clip_end: Clip end time in absolute VOD seconds.
        max_count: Maximum number of punch-in windows to return.
        min_gap_s: Minimum gap between peaks in seconds.
        window_s: Width of each punch-in window in seconds.
        threshold_pct: Percentile threshold for peak detection (0-100).

    Returns:
        List of PunchWindow objects with times relative to clip start, sorted by start time.
    """
    dedans = [(t, s) for t, s in series if clip_start <= t <= clip_end]
    if len(dedans) < 1:
        return []
    scores = sorted(s for _, s in dedans)
    seuil = scores[min(len(scores) - 1, int(len(scores) * threshold_pct / 100))]
    if scores[-1] - statistics.median(scores) < MIN_PROMINENCE:
        return []  # signal plat ou simple bruit : zoomer sur rien est pire que ne pas zoomer
    pics = sorted((s, t) for t, s in dedans if s >= seuil and s > scores[0])
    retenus: list[float] = []
    for s, t in reversed(pics):                  # du plus fort au plus faible
        if all(abs(t - r) >= min_gap_s for r in retenus):
            retenus.append(t)
        if len(retenus) == max_count:
            break
    fenetres = []
    for t in sorted(retenus):
        debut = max(0.0, t - clip_start - 0.2)
        fin = min(clip_end - clip_start, debut + window_s)
        if fin > debut:
            fenetres.append(PunchWindow(round(debut, 3), round(fin, 3)))
    return fenetres


def legacy_zoom_expression(windows: list[PunchWindow], *, zoom: float = 1.22) -> str:
    """Generate a zoom expression string from punch-in windows (repli V5).

    Copie conforme de l'ancien punch.zoom_expression : sert de repli quand
    l'engin de mouvement organique est desactive (reglage montage=0, tranche
    par render/montage_plan.py).

    Args:
        windows: List of PunchWindow objects.
        zoom: Zoom factor (default 1.22 = 22% zoom).

    Returns:
        Expression string for use in rendering (e.g., in FFmpeg filters).
    """
    if not windows:
        return "1"
    delta = round(zoom - 1.0, 2)
    entre = "+".join(f"between(it,{w.start:.3f},{w.end:.3f})" for w in windows)
    return f"1+{delta}*({entre})"
