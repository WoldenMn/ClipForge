import itertools

from clipforge.render.montage.motion import PunchWindow, detect_punches, legacy_zoom_expression


def test_a_noise_blip_yields_no_punch():
    serie = [(float(t), 0.5) for t in range(30)]
    serie[15] = (15.0, 0.51)
    assert detect_punches(serie, clip_start=0.0, clip_end=30.0) == []


def test_flat_signal_yields_no_punch():
    serie = [(float(t), 0.5) for t in range(30)]
    assert detect_punches(serie, clip_start=0.0, clip_end=30.0) == []


def test_peaks_become_ordered_non_overlapping_windows():
    serie = [(float(t), 0.2) for t in range(40)]
    serie[8] = (8.0, 0.95); serie[9] = (9.0, 0.9)      # pic double -> UNE fenetre
    serie[30] = (30.0, 0.99)
    ws = detect_punches(serie, clip_start=0.0, clip_end=40.0)
    assert len(ws) == 2
    assert ws[0].start < ws[1].start
    assert all(a.end <= b.start for a, b in itertools.pairwise(ws))
    assert abs((ws[0].end - ws[0].start) - 1.4) < 1e-6


def test_times_are_relative_to_clip_and_clamped():
    serie = [(100.0, 0.1), (105.0, 0.99), (110.0, 0.1)]
    ws = detect_punches(serie, clip_start=104.5, clip_end=112.0)
    assert 0.0 <= ws[0].start < ws[0].end <= 7.5


def test_max_count_keeps_the_strongest():
    serie = [(float(t), 0.1) for t in range(60)]
    for t, s in [(5, .8), (15, .9), (25, .95), (35, .99), (45, .85)]:
        serie[t] = (float(t), s)
    ws = detect_punches(serie, clip_start=0.0, clip_end=60.0, max_count=3)
    assert len(ws) == 3
    assert [w.start for w in ws] == [14.8, 24.8, 34.8]


def test_zoom_expression_is_a_sum_of_betweens():
    ws = [PunchWindow(3.1, 4.5), PunchWindow(11.0, 12.4)]
    assert legacy_zoom_expression(ws) == \
        "1+0.22*(between(it,3.100,4.500)+between(it,11.000,12.400))"
    assert legacy_zoom_expression([]) == "1"
