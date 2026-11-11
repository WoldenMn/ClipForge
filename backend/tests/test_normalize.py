import pytest

from clipforge.pipeline.normalize import rank_normalize


def test_rank_normalize_spreads_values():
    out = rank_normalize([10.0, 20.0, 30.0, 40.0])
    assert out == [0.0, pytest.approx(1 / 3), pytest.approx(2 / 3), 1.0]


def test_rank_normalize_keeps_ties_equal():
    out = rank_normalize([5.0, 5.0, 9.0])
    assert out[0] == out[1] < out[2] == 1.0


def test_rank_normalize_excludes_zeros():
    # les fenetres nulles (silence total) restent a 0 et ne prennent pas de rang
    out = rank_normalize([0.0, 1.0, 2.0, 0.0])
    assert out[0] == 0.0 and out[3] == 0.0
    assert out[1] == 0.0 and out[2] == 1.0


def test_rank_normalize_edge_cases():
    assert rank_normalize([]) == []
    assert rank_normalize([7.0]) == [1.0]
    assert rank_normalize([0.0, 0.0]) == [0.0, 0.0]
    assert rank_normalize([3.0, 3.0]) == [1.0, 1.0]
