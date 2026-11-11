"""Jonction des mots de transcription en texte lisible.

faster-whisper rend l'elision et le trait d'union comme des mots separes
(« c » + « 'est », « allez » + « -vous ») : tout assemblage naif a l'espace
produit « c 'est ». Ces tests fixent le contrat de la jonction unique.
"""
from clipforge.text import is_continuation, join_words


def test_une_elision_se_colle_au_mot_precedent():
    assert join_words(["c", "'est", "pas", "moi"]) == "c'est pas moi"


def test_un_trait_d_union_se_colle_au_mot_precedent():
    assert join_words(["allez", "-vous"]) == "allez-vous"


def test_l_apostrophe_typographique_colle_comme_la_droite():
    assert join_words(["d", "’ailleurs"]) == "d’ailleurs"


def test_une_continuation_en_tete_garde_son_texte():
    """Une fenetre peut commencer au milieu d'une elision (le « c » est hors du
    segment) : le morceau restant s'ecrit tel quel, jamais avale."""
    assert join_words(["'est", "pas", "moi"]) == "'est pas moi"


def test_les_mots_ordinaires_restent_separes_par_une_espace():
    assert join_words(["Non", "Non", "c", "'etait", "quoi"]) == "Non Non c'etait quoi"


def test_les_blancs_de_bordure_et_les_mots_vides_disparaissent():
    """Un mot edite a la main peut porter des blancs : ils ne doivent ni casser
    le collage, ni laisser de double espace dans le texte rendu."""
    assert join_words([" c ", " 'est ", "", "  ", "la"]) == "c'est la"


def test_une_liste_vide_rend_une_chaine_vide():
    assert join_words([]) == ""


def test_is_continuation_reconnait_les_trois_prefixes():
    assert is_continuation("'est")
    assert is_continuation("’ailleurs")
    assert is_continuation("-vous")


def test_is_continuation_refuse_un_mot_ordinaire_ou_vide():
    assert not is_continuation("est")
    assert not is_continuation("")
    assert not is_continuation("   ")
