"""Mots de sortie du montage (render/montage/words_out.py, Task 8) : relatif au
clip, puis etalement sur la timeline de sortie avec les tiers d'emphase
alignes. Isole a dessein -- aucune DB, aucun ffmpeg -- pour couvrir le cas
subtil documente par le module : un mot duplique par le teaser doit porter son
tier sur SES DEUX copies, triees par temps de sortie.
"""
import pytest

from clipforge.pipeline.segmenter import Word
from clipforge.render.montage.edl import Edl, Segment
from clipforge.render.montage.timemap import TimeMap
from clipforge.render.montage.words_out import map_words_and_tiers, relative_words


def test_relative_words_reexprime_au_debut_du_clip():
    mots = [Word(start=10.0, end=10.4, text="il"),
           Word(start=100.123, end=100.523, text="pic")]
    rel = relative_words(mots, 10.0)
    assert [w.start for w in rel] == [0.0, pytest.approx(90.123)]
    assert [w.end for w in rel] == [pytest.approx(0.4), pytest.approx(90.523)]
    assert [w.text for w in rel] == ["il", "pic"]


def test_mot_duplique_par_le_teaser_porte_son_tier_sur_les_deux_copies():
    """Cas documente par le docstring du module : un mot repris dans le teaser
    ET garde dans le corps produit DEUX copies, chacune avec le MEME tier,
    triees par debut de sortie -- pas dans l'ordre de `words` en entree."""
    edl = Edl(teaser=Segment(9.5, 11.0), segments=[Segment(0.0, 4.0), Segment(5.5, 20.0)])
    tmap = TimeMap.from_edl(edl)
    mots = [Word(start=9.8, end=10.4, text="pic")]
    tiers = ["fort"]

    mots_sortie, tiers_sortie = map_words_and_tiers(tmap, mots, tiers)

    assert [w.text for w in mots_sortie] == ["pic", "pic"]
    assert tiers_sortie == ["fort", "fort"]
    assert mots_sortie[0].start < mots_sortie[1].start
    assert mots_sortie[0].start == pytest.approx(0.3)                      # teaser
    assert mots_sortie[1].start == pytest.approx(1.5 + 4.0 + (9.8 - 5.5))  # corps


def test_mot_coupe_ne_produit_aucune_copie_et_son_tier_disparait_avec_lui():
    edl = Edl(teaser=None, segments=[Segment(0.0, 4.0), Segment(8.0, 20.0)])
    tmap = TimeMap.from_edl(edl)
    mots = [Word(start=0.5, end=0.9, text="garde"), Word(start=5.0, end=5.4, text="coupe")]
    tiers = ["fort", "faible"]

    mots_sortie, tiers_sortie = map_words_and_tiers(tmap, mots, tiers)

    assert [w.text for w in mots_sortie] == ["garde"]
    assert tiers_sortie == ["fort"]


def test_mot_a_texte_vide_est_transporte_comme_les_autres():
    """Un mot au texte vide (tag de silence, ponctuation isolee editee par
    l'utilisateur...) suit exactement la meme projection qu'un mot normal --
    le texte n'entre dans aucune decision de coupe ou de tri ici."""
    edl = Edl(teaser=None, segments=[Segment(0.0, 10.0)])
    tmap = TimeMap.from_edl(edl)
    mots = [Word(start=1.0, end=1.4, text="")]
    tiers = ["faible"]

    mots_sortie, tiers_sortie = map_words_and_tiers(tmap, mots, tiers)

    assert len(mots_sortie) == 1
    assert mots_sortie[0].text == ""
    assert mots_sortie[0].start == pytest.approx(1.0)
    assert mots_sortie[0].end == pytest.approx(1.4)
    assert tiers_sortie == ["faible"]
