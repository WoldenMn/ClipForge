import itertools

import pytest

from clipforge.pipeline.segmenter import Word
from clipforge.render.montage.edl import MIN_OUT_S, PAD_S, Segment, build_cuts

BASSE, HAUTE = -60.0, -20.0


def _energie(clip_dur, zones_hautes):
    serie = []
    t = 0.0
    while t < clip_dur:
        haut = any(a <= t <= b for a, b in zones_hautes)
        serie.append((round(t, 3), HAUTE if haut else BASSE))
        t += 0.1
    return serie


def _mots(*bornes):
    return [Word(start=a, end=b, text=f"mot{i}") for i, (a, b) in enumerate(bornes)]


def test_trou_long_et_muet_est_coupe():
    mots = _mots((0.0, 4.0), (5.5, 20.0))          # trou de 1,5 s
    segs = build_cuts(mots, _energie(20.0, [(0, 4), (5.5, 20)]),
                      clip_dur_s=20.0, cut_style="standard")
    assert segs == [Segment(0.0, 4.0 + PAD_S), Segment(5.5 - PAD_S, 20.0)]


def test_trou_energique_est_garde():
    mots = _mots((0.0, 4.0), (5.5, 20.0))
    segs = build_cuts(mots, _energie(20.0, [(4.0, 5.5)]),   # seul le trou est habite
                      clip_dur_s=20.0, cut_style="standard")
    assert segs == [Segment(0.0, 20.0)]


def test_trou_court_sous_le_seuil_est_garde():
    mots = _mots((0.0, 4.0), (4.4, 20.0))          # 0,4 s < standard 0,55
    segs = build_cuts(mots, _energie(20.0, []), clip_dur_s=20.0,
                      cut_style="standard")
    assert segs == [Segment(0.0, 20.0)]
    nerveux = build_cuts(mots, _energie(20.0, []), clip_dur_s=20.0,
                         cut_style="nerveux")       # 0,4 s > 0,35 : coupe
    assert len(nerveux) == 2


def test_tete_et_queue_mortes_sont_coupees():
    mots = _mots((2.0, 10.0))
    segs = build_cuts(mots, _energie(12.0, [(2.0, 10.0)]),
                      clip_dur_s=12.0, cut_style="standard")
    assert segs == [Segment(2.0 - PAD_S, 10.0 + PAD_S)]


def test_garde_degeneree_rend_le_clip_entier():
    mots = _mots((0.0, 3.0), (9.0, 12.0))          # garde 6 s < MIN_OUT_S
    assert MIN_OUT_S == 8.0
    segs = build_cuts(mots, _energie(12.0, []), clip_dur_s=12.0,
                      cut_style="nerveux")
    assert segs == [Segment(0.0, 12.0)]


def test_garde_juste_sous_le_ratio_rend_le_clip_entier_meme_au_dessus_de_min_out():
    """Revue finale V6 : le test ci-dessus garde 6 s sur 12, sous MIN_OUT_S ET
    sous 60 % du clip, les deux clauses tombent ensemble ; MIN_KEEP_RATIO = 0.0
    laissait toutes les suites vertes, et 0,50 ou 0,45 passaient encore un
    premier test a 41 %. Ici 17,4 s gardees sur 30 : au-dessus de MIN_OUT_S,
    58 % du clip, juste sous les 60 %."""
    mots = _mots((0.0, 8.0), (20.84, 30.0))
    garde = (8.0 + PAD_S) + (30.0 - (20.84 - PAD_S))
    assert 8.0 < garde and 0.58 * 30.0 <= garde < 0.6 * 30.0, garde
    segs = build_cuts(mots, _energie(30.0, [(0.0, 8.0), (20.84, 30.0)]), clip_dur_s=30.0,
                      cut_style="standard")
    assert segs == [Segment(0.0, 30.0)]


def test_garde_sous_min_out_rend_le_clip_entier_meme_au_dessus_du_ratio():
    """Revue du lot B : retirer la clause MIN_OUT_S, ou l'abaisser a 5 s,
    passait partout. Ici 7,74 s gardees sur 12 : 64,5 % du clip, au-dessus du
    ratio, sous MIN_OUT_S seul."""
    mots = _mots((0.0, 3.0), (7.5, 12.0))
    garde = (3.0 + PAD_S) + (12.0 - (7.5 - PAD_S))
    assert 0.6 * 12.0 < garde < 8.0, garde
    segs = build_cuts(mots, _energie(12.0, [(0.0, 3.0), (7.5, 12.0)]), clip_dur_s=12.0,
                      cut_style="standard")
    assert segs == [Segment(0.0, 12.0)]


def test_aucun_mot_rend_le_clip_entier():
    segs = build_cuts([], _energie(30.0, []), clip_dur_s=30.0,
                      cut_style="standard")
    assert segs == [Segment(0.0, 30.0)]


def test_plancher_fusionne_les_confettis():
    # mot isole de 0,4 s entre deux trous coupables -> le confetti est
    # rattache par suppression de la coupe adjacente la plus courte
    mots = _mots((0.0, 8.0), (9.0, 9.4), (11.0, 20.0))
    segs = build_cuts(mots, _energie(20.0, []), clip_dur_s=20.0,
                      cut_style="standard")
    assert all(s.src_end - s.src_start >= 0.60 for s in segs)
    # aucune borne dans un mot
    for s in segs:
        for w in mots:
            assert not (w.start < s.src_start < w.end)
            assert not (w.start < s.src_end < w.end)


def test_plancher_fusionne_un_vrai_confetti():
    # variante ruling 2 : mot du milieu 0,3 s -> segment garde du milieu
    # 8,88-9,42 = 0,54 s < MIN_SEG_S, la fusion se declenche vraiment
    mots = _mots((0.0, 8.0), (9.0, 9.3), (11.0, 20.0))
    segs = build_cuts(mots, _energie(20.0, []), clip_dur_s=20.0,
                      cut_style="standard")
    assert all(s.src_end - s.src_start >= 0.60 for s in segs)
    for s in segs:
        for w in mots:
            assert not (w.start < s.src_start < w.end)
            assert not (w.start < s.src_end < w.end)


def test_style_inconnu_leve_une_erreur_qui_le_nomme():
    """Revue finale V6 (lot C, C7) : le coeur pur repliait en silence sur
    "standard" ; le repli vit desormais dans montage_plan, journalise."""
    mots = _mots((0.0, 4.0), (5.5, 20.0))
    with pytest.raises(ValueError, match="ce-style-n-existe-pas"):
        build_cuts(mots, _energie(20.0, [(0, 4), (5.5, 20)]),
                   clip_dur_s=20.0, cut_style="ce-style-n-existe-pas")


def test_mot_imbrique_ne_coupe_pas_a_travers_le_mot_englobant():
    # B est imbrique dans A ; le seul vrai trou est 10 -> 11 (fin de A a
    # debut de C), pas 4 -> 11 (fin de B, qui ignorerait que A continue)
    mots = _mots((0.0, 10.0), (3.0, 4.0), (11.0, 20.0))
    segs = build_cuts(mots, _energie(20.0, []), clip_dur_s=20.0,
                      cut_style="standard")
    assert segs == [Segment(0.0, 10.0 + PAD_S), Segment(11.0 - PAD_S, 20.0)]
    for s in segs:
        for w in mots:
            assert not (w.start < s.src_start < w.end)
            assert not (w.start < s.src_end < w.end)


def test_ordre_desordonne_se_comporte_comme_trie():
    mots_desordre = _mots((5.0, 8.0), (0.0, 3.0))
    mots_tries = _mots((0.0, 3.0), (5.0, 8.0))
    energie = _energie(10.0, [])
    obtenu = build_cuts(mots_desordre, energie, clip_dur_s=10.0,
                        cut_style="standard")
    attendu = build_cuts(mots_tries, energie, clip_dur_s=10.0,
                         cut_style="standard")
    assert obtenu == attendu
    for s in obtenu:
        for w in mots_desordre:
            assert not (w.start < s.src_start < w.end)
            assert not (w.start < s.src_end < w.end)


def test_garde_pile_au_seuil_flottant_nest_pas_degeneree():
    # 4.12 + (13.0 - 9.12) vaut 8.0 exact en reel mais 7.999999999999999
    # en flottant brut : sans marge, ca bascule en degenere a tort
    mots = _mots((0.0, 4.0), (9.24, 13.0))
    segs = build_cuts(mots, _energie(13.0, []), clip_dur_s=13.0,
                      cut_style="standard")
    # round() cote test : 9.24 - PAD_S porte un residu flottant
    # (9.120000000000001) que la sortie de build_cuts, elle, arrondit
    assert segs == [Segment(0.0, 4.0 + PAD_S), Segment(round(9.24 - PAD_S, 3), 13.0)]


def test_propriete_segments_valides_sur_50_dispositions():
    # 50 dispositions de mots generees par pas fixes (pas de random) :
    # segments disjoints, ordonnes, dans les bornes, aucune borne dans un mot
    styles = ["doux", "standard", "nerveux"]
    for i in range(50):
        n_mots = 2 + (i % 5)
        pas_trou = 0.2 + 0.05 * (i % 7)
        duree_mot = 0.3 + 0.02 * (i % 11)
        t = 0.5 + 0.1 * (i % 4)
        bornes = []
        for k in range(n_mots):
            bornes.append((round(t, 3), round(t + duree_mot, 3)))
            t += duree_mot + pas_trou
        clip_dur = round(t + 1.0, 3)
        mots = _mots(*bornes)
        zones_hautes = [b for j, b in enumerate(bornes) if j % 2 == 0]
        energie = _energie(clip_dur, zones_hautes)
        style = styles[i % len(styles)]

        segs = build_cuts(mots, energie, clip_dur_s=clip_dur, cut_style=style)

        for a, b in itertools.pairwise(segs):
            assert a.src_end <= b.src_start
        for s in segs:
            assert 0.0 <= s.src_start <= s.src_end <= clip_dur
            for w in mots:
                assert not (w.start < s.src_start < w.end)
                assert not (w.start < s.src_end < w.end)
