import numpy as np

from clipforge.pipeline.audio_analysis import scores_from_samples


def _signal(bursts: list[tuple[float, float]], duration=60.0, sr=16000) -> np.ndarray:
    t = np.arange(int(duration * sr)) / sr
    x = 0.03 * np.sin(2 * np.pi * 300 * t)
    for start, end in bursts:
        x[(t >= start) & (t < end)] *= 20.0
    return x.astype(np.float32)


def test_burst_windows_score_highest():
    x = _signal(bursts=[(30.0, 34.0)])
    scores = scores_from_samples(x, sr=16000, window_s=10.0, hop_s=5.0)
    best_t = max(scores, key=lambda p: p[1])[0]
    assert 25.0 <= best_t <= 35.0


def test_scores_normalized_and_cover_duration():
    x = _signal(bursts=[(10.0, 12.0), (40.0, 42.0)])
    scores = scores_from_samples(x, sr=16000, window_s=10.0, hop_s=5.0)
    assert all(0.0 <= s <= 1.0 for _, s in scores)
    assert scores[0][0] == 0.0 and scores[-1][0] >= 45.0


def test_flat_signal_gives_uniform_score():
    # signal parfaitement stationnaire : toutes les fenetres sont a egalite,
    # rank_normalize les place donc toutes au rang maximal (1.0), pas 0.0
    x = _signal(bursts=[])
    scores = scores_from_samples(x, sr=16000, window_s=10.0, hop_s=5.0)
    values = {round(s, 6) for _, s in scores}
    assert values == {1.0}


def test_no_ties_at_the_top():
    sr, dur = 16000, 210.0
    t = np.arange(int(dur * sr)) / sr
    x = ((0.01 + 0.002 * t) * np.sin(2 * np.pi * 300 * t)).astype(np.float32)
    scores = scores_from_samples(x, sr=sr, window_s=10.0, hop_s=5.0)
    top = sorted((s for _, s in scores), reverse=True)[:5]
    assert len({round(s, 6) for s in top}) == 5
