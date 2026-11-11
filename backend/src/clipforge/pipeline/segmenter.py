import itertools
from bisect import bisect_left, bisect_right
from dataclasses import dataclass

from clipforge.pipeline.normalize import rank_normalize


@dataclass
class Word:
    start: float
    end: float
    text: str


@dataclass
class Candidate:
    start_s: float
    end_s: float
    peak_s: float
    score: float


def sentence_starts(words: list[Word], *, gap_s: float = 0.8) -> list[int]:
    """Indices des mots qui ouvrent une phrase (1er mot, ou gap > gap_s avec le precedent)."""
    starts = []
    for i, w in enumerate(words):
        if i == 0 or w.start - words[i - 1].end > gap_s:
            starts.append(i)
    return starts


def compute_speech_scores(words: list[Word], *, duration_s: float, window_s: float = 10.0,
                          hop_s: float = 5.0) -> list[tuple[float, float]]:
    """Densite de parole par fenetre (mots/s, plafonne a 3 mots/s -> 1.0)."""
    starts = [w.start for w in words]
    out: list[tuple[float, float]] = []
    for i in itertools.count():
        t = i * hop_s
        if t >= max(duration_s - window_s, 0) + hop_s:
            break
        n = bisect_right(starts, t + window_s) - bisect_left(starts, t)
        out.append((t, min(1.0, (n / window_s) / 3.0)))
    return out


CONVERGENCE_THRESHOLD = 0.9
CONVERGENCE_BOOST = 1.3


def count_convergent(signals: dict[str, list[tuple[float, float]]], t: float) -> int:
    """Nombre de signaux depassant le seuil haut a l'instant t."""
    return sum(1 for series in signals.values()
               for ts, sc in series if ts == t and sc >= CONVERGENCE_THRESHOLD)


def combine_signals(signals: dict[str, list[tuple[float, float]]],
                    weights: dict[str, float]) -> list[tuple[float, float]]:
    """Somme ponderee des signaux presents, poids renormalises sur les signaux non vides.

    Bonus de convergence : quand au moins deux signaux depassent le seuil haut sur la
    meme fenetre (le streamer reagit ET le chat s'emballe), le score est multiplie par
    CONVERGENCE_BOOST, sans plafond — find_candidates ne fait que trier ces valeurs, et
    le re-etalement final (rank_normalize) les ramene dans [0.15, 1.0]. Un plafond a 1.0
    ecraserait tous les moments convergents au meme score et detruirait le classement.
    """
    present = {k: dict(v) for k, v in signals.items() if v}
    if not present:
        return []
    total_w = sum(weights.get(k, 1.0) for k in present)
    times = sorted({t for scores in present.values() for t in scores})
    out = []
    for t in times:
        base = sum(weights.get(k, 1.0) * scores.get(t, 0.0)
                   for k, scores in present.items()) / total_w
        n_conv = sum(1 for scores in present.values()
                     if scores.get(t, 0.0) >= CONVERGENCE_THRESHOLD)
        out.append((t, base * CONVERGENCE_BOOST if n_conv >= 2 else base))
    return out


LOOKBACK_S = 25.0
LOOKAHEAD_S = 15.0
DEFAULT_LEAD_S = 8.0
SILENCE_GAP_S = 1.0


def narrative_bounds(words: list[Word], *, peak_start: float, peak_end: float,
                     min_len_s: float, max_len_s: float) -> tuple[float, float]:
    """Bornes calees sur la narration plutot qu'une fenetre fixe centree sur le pic.

    Debut : le debut de phrase le plus proche dans les LOOKBACK_S precedant le pic.
    Fin : la fin de phrase la plus proche dans les LOOKAHEAD_S suivant le pic.
    """
    if not words:
        start = max(0.0, peak_start - DEFAULT_LEAD_S)
        return start, start + min_len_s
    starts_idx = sentence_starts(words)
    cand_starts = [words[i].start for i in starts_idx
                   if peak_start - LOOKBACK_S <= words[i].start <= peak_start]
    start = max(cand_starts) if cand_starts else max(0.0, peak_start - DEFAULT_LEAD_S)
    ends = [words[i - 1].end for i in starts_idx[1:]] + [words[-1].end]
    cand_ends = [e for e in ends if peak_end <= e <= peak_end + LOOKAHEAD_S]
    end = min(cand_ends) if cand_ends else peak_end
    if end - start < min_len_s:
        later = [e for e in ends if e >= start + min_len_s]
        end = min(later) if later else start + min_len_s
    if end - start > max_len_s:
        inside = [e for e in ends if start + min_len_s <= e <= start + max_len_s]
        end = max(inside) if inside else start + max_len_s
    return start, end


def trim_leading_silence(start_s: float, words: list[Word]) -> float:
    """Decale le debut si le clip s'ouvre sur plus de SILENCE_GAP_S de blanc."""
    after = [w.start for w in words if w.start >= start_s]
    if not after:
        return start_s
    first = min(after)
    return round(first - 0.3, 2) if first - start_s > SILENCE_GAP_S else start_s


MIN_DISTANCE_S = 180.0
MIN_GAP_S = 60.0        # blanc minimal entre deux clips
BUCKET_S = 600.0        # tranche de stream
PER_BUCKET = 2          # candidats max par tranche


def find_candidates(combined: list[tuple[float, float]], words: list[Word], *,
                    min_len_s: float = 20.0, max_len_s: float = 45.0,
                    top_n: int = 20, window_s: float = 10.0,
                    duration_s: float | None = None,
                    min_distance_s: float = 0.0) -> list[Candidate]:
    """Pics du score combine -> clips calés sur la narration, espaces dans le stream.

    V2 : bornes narratives (narrative_bounds) au lieu d'une fenetre fixe, coupe du
    silence de tete, et quota par tranche de 10 min (PER_BUCKET candidats max) au
    lieu d'une distance minimale par paire qui perdait les moments espaces de
    60-150 s. min_distance_s reste disponible comme plancher additionnel optionnel
    pour les appelants qui veulent l'ancien comportement (min_distance_s > 0).
    """
    if not combined:
        return []
    ranked = sorted(combined, key=lambda p: p[1], reverse=True)
    taken: list[Candidate] = []
    counts: dict[int, int] = {}
    for t, score in ranked:
        if len(taken) >= top_n or score <= 0.0:
            break
        start, end = narrative_bounds(words, peak_start=t, peak_end=t + window_s,
                                      min_len_s=min_len_s, max_len_s=max_len_s)
        start = trim_leading_silence(start, words)
        if end - start < min_len_s:
            end = start + min_len_s
        if end - start > max_len_s:
            end = start + max_len_s
        if duration_s is not None and end > duration_s:
            end = duration_s
            start = max(0.0, min(start, end - min_len_s))
            if end - start < min_len_s:
                continue
        if min_distance_s > 0 and any(abs(start - c.start_s) < min_distance_s for c in taken):
            continue
        if any(not (end <= c.start_s or c.end_s <= start) for c in taken):
            continue
        if any(start < c.end_s + MIN_GAP_S and c.start_s < end + MIN_GAP_S for c in taken):
            continue
        bucket = int(start // BUCKET_S)
        if counts.get(bucket, 0) >= PER_BUCKET:
            continue
        taken.append(Candidate(start_s=round(start, 2), end_s=round(end, 2), peak_s=t,
                               score=round(score, 4)))
        counts[bucket] = counts.get(bucket, 0) + 1
    taken.sort(key=lambda c: c.score, reverse=True)
    spread = rank_normalize([c.score for c in taken])
    return [Candidate(start_s=c.start_s, end_s=c.end_s, peak_s=c.peak_s,
                      score=round(0.15 + 0.85 * s, 4))
            for c, s in zip(taken, spread)]
