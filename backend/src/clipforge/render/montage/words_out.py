"""Mots de sortie du montage : relatifs au clip, puis etales sur la timeline
de sortie (teaser + corps) avec leurs tiers d'emphase alignes.

Extrait de render/montage_plan.py (Task 8) pour tenir son budget de lignes --
ce module n'ecrit que des Word, aucune decision de montage n'y est prise.
"""
from clipforge.pipeline.segmenter import Word
from clipforge.render.montage.timemap import TimeMap


def relative_words(words: list[Word], clip_start_s: float) -> list[Word]:
    """Mots reexprimes RELATIFS au debut du clip -- contrat commun a l'EDL, aux
    tiers d'emphase et a TimeMap (cf. render/montage/edl.py)."""
    return [Word(start=round(w.start - clip_start_s, 3), end=round(w.end - clip_start_s, 3),
                 text=w.text) for w in words]


def map_words_and_tiers(tmap: TimeMap, words: list[Word],
                        tiers: list[str]) -> tuple[list[Word], list[str]]:
    """Etale mots + tiers (memes indices que `words`) sur la timeline de sortie.

    Un mot source peut ne produire aucune copie (coupe), une (corps OU teaser)
    ou deux (duplique dans le teaser ET le corps) -- son tier est reporte tel
    quel sur chacune. Tri final par debut : l'ordre de sortie n'est pas
    garanti monotone dans l'ordre de `words` (un mot du corps peut suivre en
    source un mot que le teaser duplique et qui atterrit avant lui en sortie).
    """
    paires = [(copie, tier) for mot, tier in zip(words, tiers) for copie in tmap.map_word(mot)]
    paires.sort(key=lambda p: p[0].start)
    return [p[0] for p in paires], [p[1] for p in paires]
