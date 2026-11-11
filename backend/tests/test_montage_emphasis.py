from clipforge.pipeline.segmenter import Word
from clipforge.render.montage.emphasis import word_tiers


def _mots(*ranges: tuple[float, float]) -> list[Word]:
    return [Word(start=a, end=b, text="mot") for a, b in ranges]


def test_tiers_suivent_l_energie():
    mots = _mots((0.0, 0.5), (1.0, 1.5), (2.0, 2.5), (3.0, 3.5))
    energie = [(0.25, -50.0), (1.25, -30.0), (2.25, -28.0), (3.25, -8.0)]
    assert word_tiers(mots, energie) == ["bas", "normal", "normal", "haut"]


def test_garde_anti_plat_neutralise_tout():
    mots = _mots((0.0, 0.5), (1.0, 1.5))
    plat = [(0.25, -30.0), (1.25, -31.0)]
    assert word_tiers(mots, plat) == ["normal", "normal"]


def test_mots_vides_rend_liste_vide():
    assert word_tiers([], [(0.0, -10.0)]) == []


def test_energie_vide_neutralise_tout():
    mots = _mots((0.0, 0.5), (1.0, 1.5))
    assert word_tiers(mots, []) == ["normal", "normal"]


def test_marge_haut_refuse_un_mot_a_p75_mais_hors_marge():
    # 4 mots aux centres 0.25/1.25/2.25/3.25, echantillonnes exactement sur
    # ces energies : [-20, -20, -20, -19] -> p25=-20, p75=-19.75, mediane=-20.
    # Le dernier mot (-19) est >= p75 mais pas >= mediane + HAUT_MARGIN_DB
    # (-20 + 3 = -17) : il doit rester "normal", pas "haut" -- supprimer la
    # condition de marge (ne garder que db >= p75) le ferait passer a "haut"
    # et laisserait ce test vert, d'ou ce cas qui exerce precisement la marge.
    # Un 5e echantillon loin des mots (t=10.0, 0.0 dB) pousse spread_db (p90 -
    # mediane de la SERIE entiere) au-dessus de SPREAD_MIN_DB=6.0 sans changer
    # l'energie retenue pour chaque mot (plus proche voisin).
    mots = _mots((0.0, 0.5), (1.0, 1.5), (2.0, 2.5), (3.0, 3.5))
    energie = [(0.25, -20.0), (1.25, -20.0), (2.25, -20.0), (3.25, -19.0), (10.0, 0.0)]
    assert word_tiers(mots, energie) == ["bas", "bas", "bas", "normal"]
