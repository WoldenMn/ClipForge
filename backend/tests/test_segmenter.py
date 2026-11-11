from itertools import pairwise

import pytest

from clipforge.pipeline.segmenter import (
    Word,
    combine_signals,
    compute_speech_scores,
    count_convergent,
    find_candidates,
    narrative_bounds,
    sentence_starts,
    trim_leading_silence,
)


def _words(spec: list[tuple[float, float, str]]) -> list[Word]:
    return [Word(start=s, end=e, text=t) for s, e, t in spec]


def test_sentence_starts_on_gaps():
    words = _words([(0.0, 0.3, "salut"), (0.35, 0.6, "tout"), (0.65, 0.9, "le"),
                    (0.95, 1.2, "monde"), (3.0, 3.3, "alors"), (3.35, 3.7, "la")])
    assert sentence_starts(words, gap_s=0.8) == [0, 4]


def test_combine_signals_renormalizes_missing():
    audio = [(0.0, 1.0), (5.0, 0.0)]
    combined = combine_signals({"audio": audio, "chat": [], "speech": []},
                               {"audio": 1.0, "chat": 1.0, "speech": 0.5})
    assert combined[0] == (0.0, 1.0)  # chat/speech absents -> poids renormalises


def test_convergence_boosts_multi_signal_moment():
    signals = {
        "audio": [(0.0, 0.95), (5.0, 0.9)],
        "chat": [(0.0, 0.95), (5.0, 0.1)],
    }
    weights = {"audio": 1.0, "chat": 1.0}
    combined = dict(combine_signals(signals, weights))
    # t=0 : deux signaux a 0.95 -> moyenne 0.95 boostee, sans plafond
    # t=5 : un seul signal fort -> moyenne 0.5 non boostee
    assert combined[0.0] > combined[5.0]
    assert combined[0.0] == pytest.approx(0.95 * 1.3)
    assert combined[5.0] == pytest.approx(0.5)


def test_convergence_does_not_flatten_top():
    # deux moments convergents d'intensites DIFFERENTES doivent rester distinguables
    signals = {
        "audio": [(0.0, 1.0), (5.0, 0.95)],
        "chat": [(0.0, 1.0), (5.0, 0.92)],
    }
    combined = dict(combine_signals(signals, {"audio": 1.0, "chat": 1.0}))
    assert combined[0.0] != combined[5.0]


def test_count_convergent():
    signals = {"audio": [(0.0, 0.9)], "chat": [(0.0, 0.92)], "speech": [(0.0, 0.2)]}
    assert count_convergent(signals, 0.0) == 2
    assert count_convergent(signals, 99.0) == 0


def test_speech_scores_density():
    words = _words([(float(i), float(i) + 0.4, "mot") for i in range(20)])
    scores = compute_speech_scores(words, duration_s=60.0, window_s=10.0, hop_s=5.0)
    dense = dict(scores)[0.0]
    silent = dict(scores)[45.0]
    assert dense > silent


def test_narrative_bounds_uses_sentence_start_before_peak():
    words = _words([(100.0, 100.4, "alors"), (100.5, 100.9, "la"),
                    (110.0, 110.4, "je"), (110.5, 111.0, "vous"),
                    (130.0, 130.5, "raconte")])
    # pic a 112 : debut de phrase le plus proche dans les 25 s avant = 110.0
    start, end = narrative_bounds(words, peak_start=112.0, peak_end=122.0,
                                  min_len_s=20.0, max_len_s=45.0)
    assert start == 110.0
    assert 20.0 <= end - start <= 45.0


def test_narrative_bounds_without_words_falls_back():
    start, end = narrative_bounds([], peak_start=50.0, peak_end=60.0,
                                  min_len_s=20.0, max_len_s=45.0)
    assert start == 42.0  # peak_start - 8.0
    assert end - start >= 20.0


def test_narrative_bounds_respects_max_len():
    words = _words([(0.0 + i * 0.5, 0.3 + i * 0.5, "w") for i in range(400)])
    start, end = narrative_bounds(words, peak_start=100.0, peak_end=110.0,
                                  min_len_s=20.0, max_len_s=45.0)
    assert end - start <= 45.0


def test_trim_leading_silence():
    words = _words([(53.0, 53.4, "enfin"), (54.0, 54.5, "bref")])
    # le clip commence a 50 mais le premier mot arrive a 53 -> on decale
    assert trim_leading_silence(50.0, words) == pytest.approx(52.7)
    # moins d'une seconde de blanc : on ne touche pas
    assert trim_leading_silence(52.5, words) == 52.5
    assert trim_leading_silence(50.0, []) == 50.0


def test_find_candidates_bounds_and_snapping():
    # Pic de score a t=100 ; phrases commencant a 88.0 et finissant vers 132
    words = _words([(80.0 + i * 1.0, 80.4 + i * 1.0, f"w{i}") for i in range(60)])
    combined = [(float(t), 1.0 if 95 <= t <= 105 else 0.1) for t in range(0, 200, 5)]
    cands = find_candidates(combined, words, min_len_s=20.0, max_len_s=60.0, top_n=5)
    assert cands, "au moins un candidat"
    best = cands[0]
    assert 20.0 <= best.end_s - best.start_s <= 45.0
    assert best.start_s <= 100.0 <= best.end_s  # le pic est couvert


def test_find_candidates_no_overlap():
    words = _words([(float(i), float(i) + 0.4, "w") for i in range(300)])
    combined = [(float(t), 1.0 if t in (50, 55, 150, 155) else 0.05)
                for t in range(0, 300, 5)]
    # pics a moins de 180 s les uns des autres : min_distance_s=0.0 pour tester
    # l'invariant de non-chevauchement seul, independamment du filtre de diversite
    cands = find_candidates(combined, words, min_len_s=20.0, max_len_s=60.0, top_n=10,
                            min_distance_s=0.0)
    for a in cands:
        for b in cands:
            if a is not b:
                assert a.end_s <= b.start_s or b.end_s <= a.start_s


def test_find_candidates_distant_highlights_both_kept():
    # A4 : l'ancien seuil score >= 0.9 sur le 2e candidat depend du NOMBRE de
    # candidats apres re-etalement (A3), pas de la qualite du moment. L'invariant
    # reel : les deux moments forts sont bien couverts par un clip chacun, et
    # occupent les rangs 1 et 2 (aucun des deux n'est enterre par l'autre).
    words = _words([(float(i), float(i) + 0.4, "w") for i in range(500)])
    combined = [(float(t), 1.0 if 95 <= t <= 105 or 300 <= t <= 310 else 0.05)
                for t in range(0, 500, 5)]
    cands = find_candidates(combined, words, min_len_s=20.0, max_len_s=60.0, top_n=10)
    top2 = cands[:2]
    assert len(top2) == 2
    assert top2[0].start_s <= 100.0 <= top2[0].end_s
    assert top2[1].start_s <= 300.0 <= top2[1].end_s


def test_find_candidates_clamped_to_duration():
    words = _words([(float(i), float(i) + 0.4, "w") for i in range(3600)])
    combined = [(float(t), 1.0 if t == 3590 else 0.05) for t in range(0, 3595, 5)]
    cands = find_candidates(combined, words, min_len_s=20.0, max_len_s=60.0,
                            top_n=5, duration_s=3600.0)
    best = cands[0]
    assert best.end_s <= 3600.0
    assert best.end_s - best.start_s >= 20.0


def test_find_candidates_plateau_yields_single_clip():
    # Plateau de 6 fenetres a 1.0 : un seul moment, pas de fragmentation par rognage
    words = _words([(float(i), float(i) + 0.4, "w") for i in range(300)])
    combined = [(float(t), 1.0 if 100 <= t <= 125 else 0.05) for t in range(0, 300, 5)]
    cands = find_candidates(combined, words, min_len_s=20.0, max_len_s=60.0, top_n=10)
    high = [c for c in cands if c.score == 1.0]
    assert len(high) == 1, f"plateau fragmente: {high}"
    assert high[0].start_s <= 112.5 <= high[0].end_s


def test_find_candidates_enforces_min_distance():
    # A2 : le quota par tranche est desormais le mecanisme par defaut (min_distance_s
    # vaut 0.0 par defaut). Ce test verifie le plancher LEGACY, disponible en options
    # en passant min_distance_s explicitement (>0 reactive l'ancienne contrainte par
    # paire, en plus du quota).
    words = _words([(float(i), float(i) + 0.4, "w") for i in range(600)])
    # deux pics separes de 60 s seulement : un seul candidat doit sortir
    combined = [(float(t), 1.0 if t in (100, 160) else 0.05) for t in range(0, 600, 5)]
    cands = find_candidates(combined, words, top_n=10, min_distance_s=180.0)
    starts = sorted(c.start_s for c in cands)
    for a, b in pairwise(starts):
        assert b - a >= 180.0


def test_find_candidates_durations_in_new_range():
    words = _words([(float(i), float(i) + 0.4, "w") for i in range(900)])
    combined = [(float(t), 1.0 if t % 300 == 0 else 0.05) for t in range(0, 900, 5)]
    cands = find_candidates(combined, words, top_n=10)
    assert cands
    for c in cands:
        assert 20.0 <= c.end_s - c.start_s <= 45.0


def test_find_candidates_peak_s_locks_convergent_count():
    # A1 : peak_s doit rester l'instant reel du pic, pas start_s (debut de PHRASE
    # jusqu'a 25 s avant le pic). Deux signaux forts a t=300 ; la phrase qui couvre
    # le pic commence 20 s avant (t=280) : avec l'ancienne derivation depuis start_s,
    # count_convergent aurait cherche a t=280 et trouve 0 au lieu de 2.
    t = 300.0
    signals = {"audio": [(t, 0.95)], "chat": [(t, 0.93)]}
    combined = combine_signals(signals, {"audio": 1.0, "chat": 1.0})
    words = _words([(t - 20.0 + i, t - 20.0 + i + 0.4, "w") for i in range(100)])
    cands = find_candidates(combined, words, top_n=5)
    assert len(cands) == 1
    cand = cands[0]
    assert cand.start_s == pytest.approx(t - 20.0)
    assert count_convergent(signals, cand.peak_s) == 2
    assert count_convergent(signals, cand.start_s) == 0


def test_candidate_scores_are_spread():
    # 5 pics distincts, chacun dans sa propre tranche de 600 s (le quota par tranche
    # de A2 n'en ecarte donc aucun) : le ré-étalement doit les repartir sur toute
    # l'echelle [0.15, 1.0] (A3 : le pire score n'est plus 0 a l'affichage), pas les
    # saturer a 1.0 comme des scores lies l'auraient fait.
    words = _words([(float(i), float(i) + 0.4, "w") for i in range(3000)])
    peaks = {100: 0.95, 800: 0.94, 1500: 0.93, 2200: 0.92, 2900: 0.91}
    combined = [(float(t), peaks.get(t, 0.05)) for t in range(0, 3000, 5)]
    cands = find_candidates(combined, words, top_n=5)
    scores = [c.score for c in cands]
    assert len(scores) == 5
    assert scores[0] == 1.0 and scores[-1] == 0.15
    assert len(set(scores)) == len(scores)
