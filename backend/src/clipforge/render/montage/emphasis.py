"""Tiers d'emphase vocale par mot, derives de l'energie fine (Task 5, energy.py).

Le spread p90-mediane de la serie d'energie sert de garde anti-plat : sous
SPREAD_MIN_DB, la parole est jugee trop uniforme pour justifier une emphase et
tous les mots restent "normal". Sinon, les quantiles portent sur les energies
DES MOTS (pas sur la serie brute) : un mot dont l'energie est <= p25 devient
"bas", et un mot >= p75 ET >= mediane + HAUT_MARGIN_DB devient "haut" (la
double condition evite qu'un mot en simple queue de distribution plate se
fasse passer pour un pic). L'energie d'un mot est celle de l'echantillon dont
le centre de fenetre est le plus proche du centre du mot -- jamais une
moyenne sur l'intervalle (mean_db_between rend None sur des plages courtes).

Ce module nomme les tiers, captions_pop.py les rend (couleurs/tailles ASS
prives, non exportes ici) : le contrat partage entre les deux est le
vocabulaire "bas"/"normal"/"haut", pas les valeurs pixel.
"""
import numpy as np

from clipforge.pipeline.segmenter import Word
from clipforge.render.montage.energy import spread_db

SPREAD_MIN_DB = 6.0
HAUT_MARGIN_DB = 3.0


def _nearest_db(t: float, energy: list[tuple[float, float]]) -> float:
    """Energie de l'echantillon dont le centre de fenetre est le plus proche de t."""
    return min(energy, key=lambda sample: abs(sample[0] - t))[1]


def word_tiers(words: list[Word], energy: list[tuple[float, float]]) -> list[str]:
    """Un tier ('bas' | 'normal' | 'haut') par mot, meme longueur/ordre que words.
    Cas n=1 : les quantiles s'ecrasent sur l'unique valeur (p25=p75=mediane),
    donc db <= p25 est toujours vrai et db >= mediane + 3 jamais -- un mot
    seul rend systematiquement "bas", resultat mathematiquement force."""
    if not words:
        return []
    if not energy or spread_db(energy) < SPREAD_MIN_DB:
        return ["normal"] * len(words)

    word_dbs = [_nearest_db((w.start + w.end) / 2.0, energy) for w in words]
    p25 = float(np.percentile(word_dbs, 25))
    p75 = float(np.percentile(word_dbs, 75))
    median = float(np.median(word_dbs))

    tiers = []
    for db in word_dbs:
        if db <= p25:
            tiers.append("bas")
        elif db >= p75 and db >= median + HAUT_MARGIN_DB:
            tiers.append("haut")
        else:
            tiers.append("normal")
    return tiers
