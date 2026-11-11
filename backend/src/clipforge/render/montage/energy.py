"""Serie d'energie fine d'un extrait audio : la resolution que la base n'a pas.

La serie `signals` en base (fenetres de 10 s, pas de 5 s, normalisee par rang)
sait designer les pics d'un stream ; elle ne sait pas juger un trou de 0,5 s ni
l'energie d'un mot de 300 ms. Ce module travaille en dB bruts, pas en rangs :
les gardes anti-plat comparent des contrastes reels, et une normalisation par
rang rendrait tous les clips faussement contrastes.

Fenetre = 0,15 s, pas = 0,05 s. Chaque echantillon est horodate au CENTRE
de sa fenetre (+/−75 ms autour du timestamp).
"""
import numpy as np

WINDOW_S = 0.15
HOP_S = 0.05
_FLOOR_DB = -80.0


def fine_energy(samples: np.ndarray, sr: int, *, window_s: float = WINDOW_S,
                hop_s: float = HOP_S) -> list[tuple[float, float]]:
    win, hop = max(1, int(window_s * sr)), max(1, int(hop_s * sr))
    if len(samples) < win:
        samples = np.pad(samples, (0, win - len(samples)))
    starts = range(0, max(1, len(samples) - win + 1), hop)
    out = []
    for s in starts:
        rms = float(np.sqrt(np.mean(samples[s:s + win] ** 2)))
        db = 20.0 * np.log10(rms) if rms > 0 else _FLOOR_DB
        t = (s + win / 2) / sr
        out.append((t, max(_FLOOR_DB, db)))
    return out


def median_db(series: list[tuple[float, float]]) -> float:
    return float(np.median([s for _, s in series]))


def spread_db(series: list[tuple[float, float]]) -> float:
    vals = [s for _, s in series]
    return float(np.percentile(vals, 90) - np.median(vals))


def mean_db_between(series: list[tuple[float, float]], t0: float,
                    t1: float) -> float | None:
    """Moyenne dB des echantillons dont le centre est dans [t0, t1].

    Les timestamps sont au CENTRE de la fenetre (±75 ms autour).
    Une plage >= 0,1 s garantit au moins un echantillon quelle que soit
    la phase de la grille. Les requetes ponctuelles rendent None.
    """
    vals = [s for t, s in series if t0 <= t <= t1]
    return float(np.mean(vals)) if vals else None
