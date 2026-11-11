import random
from itertools import pairwise

from clipforge.pipeline.segmenter import Word
from clipforge.render.montage.coldopen import (
    _EPS,
    TEASER_MAX_S,
    TEASER_MIN_S,
    choose_cold_open,
)
from clipforge.render.montage.edl import Segment, build_edl

_TOLERANCE = 1e-6  # marge de test, plus large que le _EPS interne du module

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


def test_signal_plat_ne_donne_pas_de_teaser():
    assert choose_cold_open(_mots((0.0, 30.0)), _energie(30.0, []),
                            clip_dur_s=30.0) is None


def test_pic_dans_les_trois_premieres_secondes_est_refuse():
    energie = _energie(30.0, [(1.0, 1.6)])
    assert choose_cold_open(_mots((0.0, 30.0)), energie, clip_dur_s=30.0) is None


def test_pic_net_donne_un_teaser_de_mots_entiers():
    mots = _mots((0.0, 6.0), (6.2, 9.6), (9.8, 10.4), (10.6, 11.2), (11.4, 30.0))
    energie = _energie(30.0, [(9.9, 10.3)])
    t = choose_cold_open(mots, energie, clip_dur_s=30.0)
    # coeur = (9.8, 10.4) ; extension droite : 0.6+0.2+0.6 = 1.4 <= 1.8 ;
    # gauche (3.4 s) et droite suivante (18.6 s) ne tiennent plus ;
    # respiration 0.12 des deux cotes dans les trous libres
    assert t == Segment(9.68, 11.32)
    assert 1.2 <= t.src_end - t.src_start <= 1.8
    assert t.src_start <= 9.9 <= t.src_end
    for w in mots:
        assert not (w.start < t.src_start < w.end)
        assert not (w.start < t.src_end < w.end)


def test_pic_dans_un_mot_geant_renonce():
    mots = _mots((0.0, 6.0), (6.2, 14.8), (15.0, 30.0))
    energie = _energie(30.0, [(9.8, 10.6)])
    assert choose_cold_open(mots, energie, clip_dur_s=30.0) is None


def test_pic_sans_mots_donne_la_fenetre_brute():
    energie = _energie(30.0, [(9.8, 10.6)])
    t = choose_cold_open([], energie, clip_dur_s=30.0)
    # derive flottante du helper _energie : premiere HAUTE a 9.9, pas 9.8
    assert t == Segment(9.3, 10.5)


def test_teaser_trop_court_renonce():
    # mot coeur isole de 0.6 s, voisins trop loin pour l'extension,
    # respiration 0.12+0.12 -> 0.84 s < 1.2 : pas de teaser
    mots = _mots((0.0, 6.0), (9.8, 10.4), (14.0, 30.0))
    energie = _energie(30.0, [(9.9, 10.3)])
    assert choose_cold_open(mots, energie, clip_dur_s=30.0) is None


def test_build_edl_assemble_teaser_et_coupes():
    mots = _mots((0.0, 4.0), (5.5, 9.6), (9.8, 11.2), (11.4, 20.0))
    energie = _energie(20.0, [(9.9, 10.3)])
    edl = build_edl(mots, energie, clip_dur_s=20.0, cut_style="standard")
    assert edl.teaser == Segment(9.68, 11.32)
    assert len(edl.segments) == 2          # seule la coupe du trou 4.0 -> 5.5
    off = build_edl(mots, energie, clip_dur_s=20.0, cut_style="standard",
                    cold_open=False)
    assert off.teaser is None
    assert off.segments == edl.segments


def test_extension_droite_ne_regresse_pas_dans_un_mot_niche():
    # D=(5.11,6.5) est le coeur (plus grand recouvrement que le mot de
    # 4.9-5.1) ; E=(6.1,6.2) est NICHE dans D (trie par start seulement,
    # E vient apres D dans la liste triee mais E.end < D.end). Avant le
    # correctif max/min, l'extension droite regressait fin de 6.5 (D) a
    # 6.2 (E), produisant Segment(4.78, 6.32) qui tranche D en son milieu.
    mots = _mots((4.9, 5.1), (5.11, 6.5), (6.1, 6.2))
    energie = _energie(30.0, [(4.95, 5.05)])
    t = choose_cold_open(mots, energie, clip_dur_s=30.0)
    # contrat complet : soit None, soit un Segment valide de bout en bout
    if t is not None:
        assert TEASER_MIN_S - _TOLERANCE <= t.src_end - t.src_start <= TEASER_MAX_S + _TOLERANCE
        assert t.src_start <= 5.0 <= t.src_end
        assert 0.0 <= t.src_start < t.src_end <= 30.0
        for w in mots:
            assert not (w.start < t.src_start < w.end)
            assert not (w.start < t.src_end < w.end)
    # comportement fige : valeur exacte produite par l'implementation corrigee
    assert t == Segment(4.78, 6.58)


def test_mot_qui_deborde_le_clip_ne_produit_pas_de_segment_hors_bornes():
    # le mot (9.0, 10.6) deborde deja clip_dur_s=10.0 ; avant le correctif
    # de clamp final, le teaser rendait Segment(8.88, 10.6) avec
    # src_end > clip_dur_s. Apres clamp a 10.0, la fenetre ne respire plus
    # que jusqu'a 1.12 s (< TEASER_MIN_S) : aucun teaser ne satisfait le
    # contrat, donc None (le None suffit a prouver qu'aucun Segment hors
    # bornes ne peut sortir : il n'y a pas de Segment du tout).
    mots = _mots((9.0, 10.6))
    energie = _energie(10.0, [(9.4, 9.6)])
    assert choose_cold_open(mots, energie, clip_dur_s=10.0) is None


def test_mot_coeur_pile_a_teaser_max_s_tolere_le_bruit_flottant():
    # 5.9 - 4.1 vaut 1.8000000000000007 en flottant, pas 1.8 exact ; sans
    # tolerance, la garde "mot coeur trop long" rejetait ce mot ordinaire
    # a tort. Un seul mot, aucun voisin : pas d'extension, budget de
    # respiration nul (1.8 - longueur ~ 0), fenetre = le mot tel quel.
    mots = _mots((4.1, 5.9))
    energie = _energie(30.0, [(4.95, 5.05)])
    assert choose_cold_open(mots, energie, clip_dur_s=30.0) == Segment(4.1, 5.9)


def _energie_pic(clip_dur, pic_t):
    """Energie plate a -50 dB, un seul pic a 0 dB a `pic_t` (grille de 0,05 s)."""
    return [(round(i * 0.05, 3), 0.0 if i == round(pic_t / 0.05) else -50.0)
            for i in range(round(clip_dur / 0.05))]


def _ne_coupe_aucun_mot(segment, mots):
    return all(not (w.start < segment.src_start < w.end)
               and not (w.start < segment.src_end < w.end) for w in mots)


def test_mot_niche_dans_son_voisin_gauche_ne_fait_pas_trancher_ce_voisin():
    # N niche dans A (trie par start, N suit A mais finit avant lui) : le bord
    # gauche se calait sur la fin de N (9.7) et non sur celle de A (9.9), et la
    # respiration mordait dans A -> Segment(9.88, 11.62). A et N forment un
    # seul amas : sortie identique au temoin sans N.
    mots = _mots((9.0, 9.9), (9.05, 9.7), (10.0, 10.6), (10.9, 11.5))
    temoin = _mots((9.0, 9.9), (10.0, 10.6), (10.9, 11.5))
    energie = _energie_pic(30.0, 11.2)
    assert choose_cold_open(temoin, energie, clip_dur_s=30.0) == Segment(9.9, 11.62)
    assert choose_cold_open(mots, energie, clip_dur_s=30.0) == Segment(9.9, 11.62)


def test_mot_niche_absorbe_seul_ne_fait_pas_ouvrir_le_teaser_au_milieu_de_son_hote():
    # N niche dans A ; l'extension absorbait N seul et ouvrait le teaser a 9.6,
    # au milieu de A -> Segment(9.6, 11.12). Avec l'amas A+N (1,05 s), C seul
    # plus sa respiration ne fait que 1,07 s et C+amas 2,0 s : aucun teaser de
    # mots entiers ne tient dans [1,2 ; 1,8], donc None, comme le temoin sans N.
    mots = _mots((9.0, 10.05), (9.6, 9.8), (10.1, 11.0))
    temoin = _mots((9.0, 10.05), (10.1, 11.0))
    energie = _energie_pic(30.0, 10.5)
    assert choose_cold_open(temoin, energie, clip_dur_s=30.0) is None
    assert choose_cold_open(mots, energie, clip_dur_s=30.0) is None


def test_amas_plus_long_que_le_budget_joue_le_mot_geant():
    # Deux mots de 1,2 s qui se chevauchent : chacun tient dans TEASER_MAX_S,
    # leur amas (9.0, 11.2) non. Le pic tombe dedans : aucun teaser de mots
    # entiers possible, comme pour un mot geant.
    mots = _mots((9.0, 10.2), (10.0, 11.2))
    assert choose_cold_open(mots, _energie_pic(30.0, 10.1), clip_dur_s=30.0) is None


def test_mot_a_duree_negative_ne_fait_pas_reculer_la_fin_du_teaser():
    # (7.0, 6.6) finit avant de commencer. Absorbe tel quel a droite, il
    # ramenait la fin du teaser a 6.6 puis, respiration comprise, a 6.72, au
    # milieu de (5.19, 6.83). Ramene a un mot de duree nulle (7.0, 7.0), il ne
    # change rien : sortie du temoin sans lui.
    mots = _mots((4.12, 5.19), (5.19, 6.83), (7.0, 6.6))
    temoin = _mots((4.12, 5.19), (5.19, 6.83))
    energie = _energie_pic(30.0, 5.25)
    assert choose_cold_open(temoin, energie, clip_dur_s=30.0) == Segment(5.19, 6.95)
    assert choose_cold_open(mots, energie, clip_dur_s=30.0) == Segment(5.19, 6.95)


def _mots_edites(rng, pic_t, clip_dur):
    """Mots sur une grille de 0,01 s autour du pic, avec chevauchements (ecart
    negatif), mots niches et mots a duree negative (words_json edite par
    l'utilisateur, que l'API ne valide qu'en type), tous dans le clip comme
    apres montage_inputs.clip_words (qui ne filtre que start >= debut et
    end <= fin du clip)."""
    mots = []
    t = round(max(0.0, pic_t - rng.uniform(1.5, 3.0)), 2)
    while t < min(clip_dur, pic_t + 3.0):
        fin = min(clip_dur, round(t + rng.uniform(0.1, 1.0), 2))
        if fin > t:
            mots.append(Word(start=t, end=fin, text="mot"))
            if rng.random() < 0.5:
                a = round(rng.uniform(t, fin), 2)
                b = min(clip_dur, round(rng.uniform(a, fin + rng.choice([0.0, 0.0, 0.3])), 2))
                if b > a:
                    mots.append(Word(start=a, end=b, text="niche"))
            if rng.random() < 0.2:
                mots.append(Word(start=fin, end=round(fin - rng.uniform(0.05, 0.6), 2),
                                 text="inverse"))
        t = max(0.0, round(fin + rng.uniform(-0.3, 0.5), 2))
    rng.shuffle(mots)
    return mots


def test_propriete_aucun_teaser_ne_coupe_un_mot_meme_imbrique():
    rng = random.Random(20260918)
    clip_dur, segments, chevauchants, inverses = 30.0, 0, 0, 0
    for _ in range(400):
        pic_t = round(rng.randrange(80, 560) * 0.05, 2)
        mots = _mots_edites(rng, pic_t, clip_dur)
        tries = sorted(mots, key=lambda w: w.start)
        chevauchants += any(b.start < a.end for a, b in pairwise(tries))
        inverses += any(w.end < w.start for w in mots)
        t = choose_cold_open(mots, _energie_pic(clip_dur, pic_t), clip_dur_s=clip_dur)
        if t is None:
            continue
        segments += 1
        assert _ne_coupe_aucun_mot(t, mots), (pic_t, tries, t)
        assert 0.0 <= t.src_start < t.src_end <= clip_dur, (pic_t, tries, t)
        assert TEASER_MIN_S - _EPS <= t.src_end - t.src_start <= TEASER_MAX_S + _EPS, (
            pic_t, tries, t)
        assert t.src_start <= pic_t <= t.src_end, (pic_t, tries, t)
    assert chevauchants >= 300 and segments >= 200 and inverses >= 200, (
        chevauchants, segments, inverses)
