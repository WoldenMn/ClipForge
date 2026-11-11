import pytest

from clipforge.pipeline.segmenter import Word
from clipforge.render.montage.edl import Edl, Segment
from clipforge.render.montage.timemap import TimeMap


def test_duree_de_sortie_et_projection():
    edl = Edl(teaser=None, segments=[Segment(0.0, 4.12), Segment(5.38, 20.0)])
    tm = TimeMap.from_edl(edl)
    assert tm.output_duration == pytest.approx(4.12 + 14.62)
    assert tm.to_output(2.0) == pytest.approx(2.0)
    assert tm.to_output(5.38) == pytest.approx(4.12)
    assert tm.to_output(10.0) == pytest.approx(4.12 + 4.62)
    assert tm.to_output(4.8) is None                        # dans la coupe


def test_teaser_duplique_le_pic():
    edl = Edl(teaser=Segment(9.5, 11.0),
              segments=[Segment(0.0, 4.0), Segment(5.5, 20.0)])
    tm = TimeMap.from_edl(edl)
    assert tm.output_duration == pytest.approx(1.5 + 4.0 + 14.5)
    mot_pic = Word(start=9.8, end=10.4, text="pic")
    copies = tm.map_word(mot_pic)
    assert len(copies) == 2                                 # teaser puis corps
    assert copies[0].start == pytest.approx(0.3)
    assert copies[1].start == pytest.approx(1.5 + 4.0 + (9.8 - 5.5))
    assert copies[0].text == copies[1].text == "pic"


def test_mot_coupe_disparait_et_fenetre_coupee_rend_none():
    edl = Edl(teaser=None, segments=[Segment(0.0, 4.0), Segment(8.0, 20.0)])
    tm = TimeMap.from_edl(edl)
    assert tm.map_word(Word(start=5.0, end=6.0, text="x")) == []
    assert tm.map_window(4.5, 6.5) is None                  # centre 5,5 dans la coupe
    assert tm.map_window(8.2, 9.6) == (pytest.approx(4.2), pytest.approx(5.6))


def test_cut_times_ignore_les_decoupages_contigus():
    edl = Edl(teaser=Segment(9.5, 11.0),
              segments=[Segment(0.0, 4.0), Segment(4.0, 8.0), Segment(10.0, 20.0)])
    tm = TimeMap.from_edl(edl)
    # raccord teaser->corps a 1,5 ; jonction 4.0/4.0 contigue (pas une coupe) ;
    # jonction 8.0 -> 10.0 = vraie coupe, a 1,5 + 8,0
    assert tm.cut_times() == [pytest.approx(1.5), pytest.approx(9.5)]


def test_propriete_monotonie_et_couverture():
    edl = Edl(teaser=None, segments=[Segment(0.0, 3.3), Segment(4.4, 9.9),
                                     Segment(11.0, 15.0)])
    tm = TimeMap.from_edl(edl)
    sorties = [tm.to_output(t / 10) for t in range(0, 151)]  # noqa: PIE808 -- code du brief, verbatim
    valides = [s for s in sorties if s is not None]
    assert valides == sorted(valides)
    assert valides[-1] <= tm.output_duration + 1e-9


def test_mot_a_cheval_se_rattache_au_segment_de_son_centre():
    edl = Edl(teaser=None, segments=[Segment(0.0, 4.0), Segment(8.0, 20.0)])
    tm = TimeMap.from_edl(edl)
    # centre 4.5 dans la coupe -> aucun rattachement
    assert tm.map_word(Word(start=3.5, end=5.5, text="cheval")) == []
    # centre 3.5 dans le premier segment -> clampe a [0, 4.0]
    copies = tm.map_word(Word(start=2.5, end=4.5, text="clampe"))
    assert len(copies) == 1
    assert copies[0].start == pytest.approx(2.5)
    assert copies[0].end == pytest.approx(4.0)


def test_to_output_frontiere_partagee_et_fin_de_clip():
    edl = Edl(teaser=None, segments=[Segment(0.0, 4.0), Segment(4.0, 10.0)])
    tm = TimeMap.from_edl(edl)
    assert tm.to_output(4.0) == pytest.approx(4.0)   # frontiere contigue : continue
    assert tm.to_output(10.0) == pytest.approx(10.0) # fin de clip incluse
    assert tm.cut_times() == []                      # decoupage contigu : pas de coupe


def test_segments_contigus_sont_fusionnes_a_la_construction():
    # decoupage administratif, pas une coupe : le mot a cheval sur 4.0
    # doit garder ses deux moities, pas se faire clamper au segment tardif
    edl = Edl(teaser=None, segments=[Segment(0.0, 4.0), Segment(4.0, 10.0)])
    tm = TimeMap.from_edl(edl)
    copies = tm.map_word(Word(start=2.0, end=6.0, text="cheval"))
    assert len(copies) == 1
    assert copies[0].start == pytest.approx(2.0)
    assert copies[0].end == pytest.approx(6.0)
    assert tm.map_window(3.9, 4.1) == (pytest.approx(3.9), pytest.approx(4.1))
    assert tm.cut_times() == []
    assert tm.to_output(4.0) == pytest.approx(4.0)


def test_quasi_contigu_sous_tolerance_est_fusionne():
    edl = Edl(teaser=None, segments=[Segment(0.0, 4.0), Segment(4.0000000005, 10.0)])
    tm = TimeMap.from_edl(edl)
    assert tm.to_output(4.0) == pytest.approx(4.0)   # plus de trou ponctuel
    assert tm.output_duration == pytest.approx(10.0, abs=1e-6)


def test_garde_teaser_sans_corps_ne_cree_pas_de_coupe_fantome():
    edl = Edl(teaser=Segment(2.0, 3.3), segments=[])
    tm = TimeMap.from_edl(edl)
    assert tm.cut_times() == []
    assert tm.output_duration == pytest.approx(1.3)


def test_segments_non_tries_leve_value_error():
    edl = Edl(teaser=None, segments=[Segment(5.0, 8.0), Segment(0.0, 4.0)])
    with pytest.raises(ValueError):
        TimeMap.from_edl(edl)


def test_segments_chevauchants_leve_value_error():
    edl = Edl(teaser=None, segments=[Segment(0.0, 5.0), Segment(3.0, 8.0)])
    with pytest.raises(ValueError):
        TimeMap.from_edl(edl)
