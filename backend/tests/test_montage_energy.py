import numpy as np
import pytest

from clipforge.render.montage.energy import fine_energy, mean_db_between, median_db, spread_db


def _sine(sr, dur_s, amp):
    t = np.arange(int(sr * dur_s)) / sr
    return (amp * np.sin(2 * np.pi * 220 * t)).astype(np.float32)


def test_fine_energy_resolution_et_temps():
    sr = 16000
    series = fine_energy(_sine(sr, 2.0, 0.5), sr)
    assert series[0][0] == pytest.approx(0.075, abs=1e-6)   # centre de premiere fenetre
    assert series[1][0] - series[0][0] == pytest.approx(0.05, abs=1e-6)  # pas de 0,05 s
    assert len(series) >= 35                                # ~ (2.0 - 0.15) / 0.05


def test_fine_energy_fort_contre_faible():
    sr = 16000
    fort = np.concatenate([_sine(sr, 1.0, 0.5), _sine(sr, 1.0, 0.005)])
    series = fine_energy(fort, sr)
    debut = mean_db_between(series, 0.0, 0.8)
    fin = mean_db_between(series, 1.2, 1.9)
    assert debut - fin > 20.0                               # 40 dB d'écart théorique


def test_spread_plat_contre_contraste():
    sr = 16000
    plat = fine_energy(_sine(sr, 2.0, 0.5), sr)
    assert spread_db(plat) < 3.0
    mixte = fine_energy(
        np.concatenate([_sine(sr, 0.5, 0.5), _sine(sr, 1.5, 0.005)]), sr)
    assert spread_db(mixte) >= 6.0
    assert median_db(plat) > median_db(mixte)


def test_mean_db_between_hors_plage_rend_none():
    series = fine_energy(_sine(16000, 1.0, 0.5), 16000)
    assert mean_db_between(series, 5.0, 6.0) is None


def test_mean_db_between_anti_bleed():
    sr = 16000
    signal = np.concatenate([_sine(sr, 1.0, 0.4), _sine(sr, 0.35, 0.001), _sine(sr, 0.65, 0.4)])
    series = fine_energy(signal, sr)
    silence_energy = mean_db_between(series, 1.0, 1.35)
    median_energy = median_db(series)
    assert silence_energy is not None
    # discrimination : ancien code a -26,875 vs seuil -35,97
    assert silence_energy < median_energy - 25.0
