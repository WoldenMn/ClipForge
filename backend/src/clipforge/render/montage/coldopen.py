"""Choix du teaser (cold open) : la meilleure amorce avant le premier mot.

Cherche le pic d'energie le plus marquant du clip, verifie qu'il n'arrive
pas dans les toutes premieres secondes (ce serait deja le debut naturel),
puis cadre une fenetre de mots ENTIERS autour de ce pic : mot coeur (le
plus recouvert par la fenetre de base), extension gloutonne vers les
voisins qui tiennent dans le budget TEASER_MAX_S, puis une respiration de
PAD_S dans l'espace libre. Un teaser mal forme (trop court, trop long, ou
qui ne couvre pas le pic) est refuse : mieux vaut aucun teaser qu'un
teaser casse.

Cet algorithme suppose des mots disjoints : les mots qui se chevauchent
(words_json edite par l'utilisateur, que rien ne valide au-dela des types)
sont d'abord fusionnes en amas (cf. _amas), qui jouent ensuite le role des
mots. Sur une transcription propre, les amas sont les mots eux-memes.

Tous les temps sont RELATIFS au debut du clip source.
"""
from typing import TYPE_CHECKING

from clipforge.pipeline.segmenter import Word
from clipforge.render.montage.energy import median_db

if TYPE_CHECKING:  # annotation seule : l'import d'execution reste differe (choose_cold_open)
    from clipforge.render.montage.edl import Segment

TEASER_HALF_S = 0.6
TEASER_MIN_S = 1.2
TEASER_MAX_S = 1.8
TEASER_MIN_PEAK_T = 3.0
PROMINENCE_DB = 6.0
# Marge de respiration autour des mots, source unique des deux usages : la
# respiration du teaser ici, la marge de coupe de edl.py, qui l'importe.
PAD_S = 0.12
# Usage local : tolerance sur les bornes de longueur du teaser (TEASER_MIN_S,
# TEASER_MAX_S), comparees a une difference de flottants deja composes -- pic,
# demi-fenetre, extensions, respiration. Sur des temps de 10 a 90 s, l'ULP va de
# 1,8e-15 a 1,4e-14 : ces compositions se trompent de l'ordre de 1e-14 ; 1e-9
# les couvre et reste six ordres de grandeur sous la milliseconde, grain des
# bornes rendues.
_EPS = 1e-9


def _amas(words: list[Word]) -> list[Word]:
    """Mots tries par debut, ceux qui se chevauchent STRICTEMENT fusionnes en
    un seul intervalle (debut du premier, fin maximale). Deux mots qui se
    touchent pile (start == end precedent) restent deux amas. Un mot qui finit
    avant de commencer compte pour une duree nulle : absorbe tel quel, sa fin
    ferait reculer la fenetre dans le mot deja pris."""
    amas: list[Word] = []
    for mot in sorted(words, key=lambda w: w.start):
        fin = max(mot.start, mot.end)
        if amas and mot.start < amas[-1].end:
            dernier = amas[-1]
            amas[-1] = Word(start=dernier.start, end=max(dernier.end, fin), text=dernier.text)
        else:
            amas.append(Word(start=mot.start, end=fin, text=mot.text))
    return amas


def choose_cold_open(words: list[Word], energy: list[tuple[float, float]], *,
                     clip_dur_s: float) -> "Segment | None":
    """Fenetre teaser autour du pic d'energie le plus marquant, ou None.

    Segment est importe ici, dans le corps : edl.py importe ce module en
    tete de fichier (choose_cold_open et PAD_S), un import croise en tete
    ici creerait un cycle au chargement. Differe, il ne s'execute qu'a
    l'appel, quand les deux modules sont deja charges. L'annotation de
    retour, elle, lit le Segment du bloc TYPE_CHECKING : aucun import de
    plus a l'execution.

    Extension et respiration partagent la meme convention deliberee :
    cote gauche essaye/consomme en premier, le reste va au cote droit.
    """
    from clipforge.render.montage.edl import Segment

    if not energy:
        return None
    mediane = median_db(energy)
    pic_t, pic_db = energy[0]
    for t, db in energy[1:]:
        if db > pic_db:
            pic_t, pic_db = t, db
    if pic_db - mediane < PROMINENCE_DB:
        return None
    if pic_t < TEASER_MIN_PEAK_T:
        return None

    def _rendre(a: float, b: float):
        # bornes +/- _EPS : b - a est une difference de flottants deja
        # composes (pic_t +/- TEASER_HALF_S, extensions, respiration), qui
        # peut manquer 1.2 ou 1.8 exact d'un ULP sans la marge
        longueur = b - a
        if not (TEASER_MIN_S - _EPS <= longueur <= TEASER_MAX_S + _EPS):
            return None
        if not (a <= pic_t <= b):
            return None
        return Segment(round(a, 3), round(b, 3))

    mots = _amas(words)
    base_debut, base_fin = pic_t - TEASER_HALF_S, pic_t + TEASER_HALF_S

    chevauchements = []
    for i, mot in enumerate(mots):
        recouvre = min(mot.end, base_fin) - max(mot.start, base_debut)
        if recouvre > 0:
            chevauchements.append((i, mot, recouvre))

    if not chevauchements:
        # cri pur dans le silence : fenetre brute, sans aimantation
        return _rendre(max(0.0, base_debut), min(clip_dur_s, base_fin))

    coeur_i, coeur, meilleur = chevauchements[0]
    for i, mot, recouvre in chevauchements[1:]:
        if recouvre > meilleur:
            coeur_i, coeur, meilleur = i, mot, recouvre
    if coeur.end - coeur.start > TEASER_MAX_S + _EPS:
        return None

    debut, fin = coeur.start, coeur.end
    gauche_i, droite_i = coeur_i - 1, coeur_i + 1
    while True:
        voisin_g = mots[gauche_i] if gauche_i >= 0 else None
        voisin_d = mots[droite_i] if droite_i < len(mots) else None
        if voisin_g is None and voisin_d is None:
            break
        candidats = []
        if voisin_g is not None:
            candidats.append(("gauche", debut - voisin_g.end, voisin_g))
        if voisin_d is not None:
            candidats.append(("droite", voisin_d.start - fin, voisin_d))
        # plus petit ecart d'abord, egalite -> gauche
        candidats.sort(key=lambda c: (c[1], c[0] != "gauche"))
        etendu = False
        for cote, ecart, mot in candidats:
            longueur = mot.end - mot.start
            if (fin - debut) + ecart + longueur <= TEASER_MAX_S:
                if cote == "gauche":
                    debut, gauche_i = mot.start, gauche_i - 1
                else:
                    fin, droite_i = mot.end, droite_i + 1
                etendu = True
                break
        if not etendu:
            break

    # Respiration : jusqu'a PAD_S de chaque bord, dans l'espace libre
    # seulement (jamais dans un mot, jamais hors clip), sans depasser le
    # budget TEASER_MAX_S restant. Bord gauche d'abord (meme convention
    # que l'extension ci-dessus), le reste du budget va au bord droit.
    budget = TEASER_MAX_S - (fin - debut)
    limite_g = mots[gauche_i].end if gauche_i >= 0 else 0.0
    marge_g = max(0.0, min(PAD_S, debut - limite_g, budget))
    debut -= marge_g
    budget -= marge_g
    limite_d = mots[droite_i].start if droite_i < len(mots) else clip_dur_s
    marge_d = max(0.0, min(PAD_S, limite_d - fin, budget))
    fin += marge_d

    return _rendre(max(0.0, debut), min(clip_dur_s, fin))
