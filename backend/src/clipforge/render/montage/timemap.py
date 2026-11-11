"""Carte temporelle unique : source vers sortie, teaser duplique, coupes localisees.

Traversee par tout systeme en aval (legendes, fenetres de zoom, cues SFX)
qui a besoin de savoir ou un instant ou un mot source atterrit dans le
montage final. Timeline de sortie = teaser (s'il existe) puis segments du
corps, bout a bout.

Tous les temps source sont RELATIFS au debut du clip source (comme Edl).
"""
from dataclasses import dataclass
from itertools import pairwise

from clipforge.pipeline.segmenter import Word
from clipforge.render.montage.edl import Edl, Segment

# Usage local : tolerance de contiguite entre deux segments (_fusionner,
# cut_times). Elle doit rester tres inferieure au plus petit trou que
# l'EDL coupe reellement -- 0,11 s, soit le seuil le plus nerveux (0,35 s) moins
# les deux marges PAD_S -- sinon deux segments reellement separes passeraient
# pour contigus et une coupe disparaitrait de cut_times. 1e-9 laisse huit ordres
# de grandeur de marge, tout en absorbant le bruit des bornes arrondies a la ms.
_EPS = 1e-9


@dataclass(frozen=True)
class TimeMap:
    edl: Edl
    _table: tuple[tuple[float, Segment], ...]  # (offset_sortie, segment) du corps, trie

    @classmethod
    def from_edl(cls, edl: Edl) -> "TimeMap":
        segments = _fusionner(edl.segments)
        _valider(segments)
        table = []
        offset = edl.teaser.src_end - edl.teaser.src_start if edl.teaser else 0.0
        for seg in segments:
            table.append((offset, seg))
            offset += seg.src_end - seg.src_start
        return cls(edl=edl, _table=tuple(table))

    @property
    def output_duration(self) -> float:
        if not self._table:
            teaser = self.edl.teaser
            return round(teaser.src_end - teaser.src_start, 3) if teaser else 0.0
        out_off, seg = self._table[-1]
        return _projeter(out_off, seg, seg.src_end)

    def to_output(self, t_src: float) -> float | None:
        """Instant source projete sur la timeline de sortie, ou None si coupe.

        Consomme par les tests seulement : la production projette des bornes
        (map_word, map_window) et lit output_duration. Les quatre passent par
        _projeter pour le corps.
        """
        localise = self._locate(t_src)
        if localise is None:
            return None
        out_off, seg = localise
        return _projeter(out_off, seg, t_src)

    def map_word(self, w: Word) -> list[Word]:
        copies = []
        teaser = self.edl.teaser
        if teaser is not None and teaser.src_start <= w.start and w.end <= teaser.src_end:
            copies.append(Word(start=round(w.start - teaser.src_start, 3),
                               end=round(w.end - teaser.src_start, 3), text=w.text))
        localise = self._locate((w.start + w.end) / 2)
        if localise is not None:
            out_off, seg = localise
            copies.append(Word(start=_projeter(out_off, seg, _clamp(w.start, seg)),
                               end=_projeter(out_off, seg, _clamp(w.end, seg)), text=w.text))
        copies.sort(key=lambda mot: mot.start)
        return copies

    def map_window(self, start: float, end: float) -> tuple[float, float] | None:
        localise = self._locate((start + end) / 2)
        if localise is None:
            return None
        out_off, seg = localise
        return (_projeter(out_off, seg, _clamp(start, seg)),
                _projeter(out_off, seg, _clamp(end, seg)))

    def cut_times(self) -> list[float]:
        coupes = []
        if self.edl.teaser is not None and self._table:
            coupes.append(round(self.edl.teaser.src_end - self.edl.teaser.src_start, 3))
        for (_, seg), (out_off_suivant, seg_suivant) in pairwise(self._table):
            if abs(seg_suivant.src_start - seg.src_end) > _EPS:
                coupes.append(round(out_off_suivant, 3))
        return sorted(coupes)

    def _locate(self, t: float) -> tuple[float, Segment] | None:
        """Segment du corps qui possede l'instant source t, ou None si coupe.

        Convention demi-ouverte [src_start, src_end) par segment ; le
        dernier segment de la table est ferme a sa fin pour que
        to_output(fin_du_clip) se projette encore. from_edl a deja fusionne
        les segments contigus : aucune frontiere partagee ne survit ici, donc
        aucun arbitrage precoce/tardif n'est necessaire.
        """
        dernier = len(self._table) - 1
        for i, (out_off, seg) in enumerate(self._table):
            if seg.src_start <= t < seg.src_end:
                return out_off, seg
            if i == dernier and t == seg.src_end:
                return out_off, seg
        return None


def _fusionner(segments: list[Segment]) -> list[Segment]:
    """Fusionne les segments consecutifs contigus (tolerance _EPS) en un seul.

    Un decoupage contigu n'est pas une coupe : sans cette fusion, un mot ou
    une fenetre a cheval sur cette frontiere administrative se ferait
    amputer par le clamp de _locate alors qu'aucune matiere n'est perdue.
    Chaine de 3+ segments mutuellement contigus geree par extension iterative.
    """
    if not segments:
        return []
    fusionnes = [segments[0]]
    for suivant in segments[1:]:
        precedent = fusionnes[-1]
        if abs(suivant.src_start - precedent.src_end) <= _EPS:
            fusionnes[-1] = Segment(precedent.src_start, suivant.src_end)
        else:
            fusionnes.append(suivant)
    return fusionnes


def _valider(segments: list[Segment]) -> None:
    """Refuse un edl corrompu plutot que de produire une carte fausse en silence."""
    for precedent, suivant in pairwise(segments):
        if suivant.src_start < precedent.src_end:
            raise ValueError("segments edl non tries ou chevauchants apres fusion")


def _projeter(out_off: float, seg: Segment, t_src: float) -> float:
    """Projection source -> sortie d'un instant du CORPS, ecrite une seule fois :
    to_output, map_word, map_window et output_duration l'appellent.

    `out_off` est l'offset de sortie du segment qui possede `t_src` (celui que
    _locate rend). Le teaser se projette a part, round(x - teaser.src_start, 3)
    (copie teaser de map_word, cut_times, output_duration sans corps).
    """
    return round(out_off + (t_src - seg.src_start), 3)


def _clamp(t: float, seg: Segment) -> float:
    return min(max(t, seg.src_start), seg.src_end)
