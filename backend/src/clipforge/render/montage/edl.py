"""Cerveau des coupes : construit l'EDL (Edit Decision List) d'un clip.

Repere les trous entre mots consecutifs (plus tete et queue) assez longs ET pas assez energiques
pour meriter une coupe, aimante chaque coupe aux bords des mots avec une marge PAD_S (definie
par coldopen, qui s'en sert aussi pour la respiration du teaser), fusionne
les segments trop courts (plancher), puis protege contre un montage degenere (trop peu de
matiere gardee) en repliant sur le clip entier.

Tous les temps sont RELATIFS au debut du clip source.
"""
from dataclasses import dataclass

from clipforge.pipeline.segmenter import Word
from clipforge.render.montage.coldopen import PAD_S, choose_cold_open
from clipforge.render.montage.energy import mean_db_between, median_db

CUT_THRESHOLDS = {"doux": 0.9, "standard": 0.55, "nerveux": 0.35}
MIN_SEG_S = 0.60
MIN_OUT_S = 8.0
MIN_KEEP_RATIO = 0.60
# Usage local : absorber le bruit de representation de la somme des durees
# gardees, comparee a MIN_OUT_S et a MIN_KEEP_RATIO (garde degeneree, plus bas).
# Sur des secondes de l'ordre de la minute, l'erreur d'une somme de flottants se
# compte en 1e-14 : 1e-9 la couvre largement, et reste six ordres de grandeur
# sous la milliseconde, le grain de tout ce que ce module produit.
_EPS = 1e-9


@dataclass(frozen=True)
class Segment:
    src_start: float
    src_end: float


@dataclass(frozen=True)
class Edl:
    teaser: Segment | None       # None si cold_open=False ou si aucun pic net
    segments: list[Segment]      # chronologiques, disjoints, dans [0, clip_dur]


def build_edl(words: list[Word], energy: list[tuple[float, float]], *,
              clip_dur_s: float, cut_style: str, cold_open: bool = True) -> Edl:
    teaser = choose_cold_open(words, energy, clip_dur_s=clip_dur_s) if cold_open else None
    return Edl(teaser, build_cuts(words, energy, clip_dur_s=clip_dur_s, cut_style=cut_style))


def build_cuts(words: list[Word], energy: list[tuple[float, float]], *,
               clip_dur_s: float, cut_style: str) -> list[Segment]:
    """Calcule les segments gardes une fois les trous morts retires.

    Style inconnu -> ValueError : le repli sur "standard" vit dans
    render.montage_plan, qui le journalise.
    Egalite moyenne/mediane = coupable ; le veto ne protege que ce qui
    depasse la mediane.
    """
    if cut_style not in CUT_THRESHOLDS:
        raise ValueError(f"cut_style inconnu : {cut_style!r} "
                         f"(connus : {', '.join(CUT_THRESHOLDS)})")
    if not words:
        return [Segment(0.0, round(clip_dur_s, 3))]
    words = sorted(words, key=lambda w: w.start)

    seuil = CUT_THRESHOLDS[cut_style]
    mediane = median_db(energy) if energy else None

    zones = []
    for brut_debut, brut_fin, pad_debut, pad_fin in _trous_candidats(words, clip_dur_s):
        if brut_fin - brut_debut <= seuil or pad_fin - pad_debut <= 0:
            continue
        moyenne = mean_db_between(energy, pad_debut, pad_fin)
        # Veto energie : un trou reste garde s'il est au moins aussi
        # energique que la mediane du clip ("silence habite"). L'egalite
        # compte comme coupable (ruling controleur, cf. rapport Task 2) :
        # en mesure reelle une egalite stricte est de mesure nulle, mais
        # dans un clip plat (fixtures de test) seule la duree doit trancher.
        if moyenne is None or (mediane is not None and moyenne <= mediane):
            zones.append((pad_debut, pad_fin))

    gardes = _complement(zones, clip_dur_s)
    gardes, zones = _appliquer_plancher(gardes, zones, clip_dur_s)

    duree_gardee = sum(fin - debut for debut, fin in gardes)
    # Marge _EPS : additionner des flottants peut manquer le seuil exact
    # d'un ULP (ex. 7.999999999999999 au lieu de 8.0), ce qui basculerait
    # a tort en degenere ; la marge absorbe ce bruit de representation.
    if duree_gardee < MIN_OUT_S - _EPS or duree_gardee < MIN_KEEP_RATIO * clip_dur_s - _EPS:
        return [Segment(0.0, round(clip_dur_s, 3))]

    return [Segment(round(debut, 3), round(fin, 3)) for debut, fin in gardes]


def _trous_candidats(words: list[Word], clip_dur_s: float):
    """Trous chronologiques : tete, entre mots, queue.

    Chaque tuple est (debut_brut, fin_brut, debut_coupe, fin_coupe) : la
    zone coupee est l'interieur du trou apres marge PAD_S. Le veto energie
    mesure cette meme zone padded, jamais le trou brut (ruling 1) ; la
    qualification par duree, elle, reste sur le trou brut.

    `words` doit deja etre trie par `start` (fait par l'appelant). Le bord
    gauche d'un trou n'est PAS la fin du mot precedent dans la liste : un
    mot imbrique ou chevauchant (entree utilisateur non fiable) rendrait ce
    bord trop tot et couperait a travers le mot englobant. On garde donc un
    curseur du point de parole le plus a droite vu jusqu'ici ; un trou n'est
    retenu que si le mot suivant commence apres ce curseur.
    """
    trous = [(0.0, words[0].start, 0.0, words[0].start - PAD_S)]
    curseur = words[0].end
    for mot in words[1:]:
        if mot.start > curseur:
            trous.append((curseur, mot.start, curseur + PAD_S, mot.start - PAD_S))
        curseur = max(curseur, mot.end)
    trous.append((curseur, clip_dur_s, curseur + PAD_S, clip_dur_s))
    return trous


def _complement(zones: list[tuple[float, float]], clip_dur_s: float):
    """Segments gardes = complementaire des zones coupees dans [0, clip_dur].

    `zones` est toujours deja chronologique (construit ainsi par les deux
    appelants), donc aucun tri n'est necessaire ici.
    """
    gardes = []
    curseur = 0.0
    for debut, fin in zones:
        if debut > curseur:
            gardes.append((curseur, debut))
        curseur = max(curseur, fin)
    if curseur < clip_dur_s:
        gardes.append((curseur, clip_dur_s))
    return gardes


def _appliquer_plancher(gardes, zones, clip_dur_s: float):
    """Fusionne les confettis.

    Tant qu'un segment garde est sous MIN_SEG_S, retire la zone coupee
    adjacente la plus courte et recalcule. Chaque iteration retire une
    zone : la boucle est donc finie.
    """
    while True:
        court = next((i for i, (debut, fin) in enumerate(gardes)
                      if fin - debut < MIN_SEG_S), None)
        if court is None or not zones:
            return gardes, zones
        debut, fin = gardes[court]
        adjacentes = [z for z in zones if z[1] == debut or z[0] == fin]
        if not adjacentes:
            return gardes, zones
        pire = min(adjacentes, key=lambda z: z[1] - z[0])
        zones = [z for z in zones if z != pire]
        gardes = _complement(zones, clip_dur_s)
