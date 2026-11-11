"""Purge des caches de jobs au demarrage : seulement ce que nous avons nomme.

Sorti de main.py (revue finale V6, fix round 1, R6), qui monte l'application
et n'a pas a porter en plus les regles de la purge.

Exception documentee (CLAUDE.md) : suppression directe sans passage par une
corbeille, car ces fichiers sont des caches NOMMES et regenerables. Ce qui
n'est pas dans ces listes est garde et journalise -- un dossier de job est
ouvert sur le disque de l'utilisateur, rien ne dit qu'il n'y a depose que nos
fichiers. Un echec de suppression se journalise aussi : une purge muette qui
n'a rien purge est un faux vert.
"""
import logging
import os
import shutil
import sqlite3
from pathlib import Path

from clipforge.render.livraison import MIS_DE_COTE
from clipforge.render.orphelins import ranger_temporaires
from clipforge.render.verrou import NOM as NOM_VERROU

logger = logging.getLogger(__name__)

CACHES_VOD = ("audio.wav", "chat.json")
CACHES_VOD_MOTIFS = ("frame*.png",)
CACHES_VOD_DOSSIERS = ("thumbs", "previews")
CACHES_CLIP = ("captions.ass", "filter_complex.txt", "clip_audio.wav", NOM_VERROU)


def purge_stale_job_caches(conn: sqlite3.Connection, jobs_dir: Path) -> None:
    """Retire les caches des jobs dont l'objet n'existe plus, et les images de
    previsualisation des VOD encore la (regenerees a la demande).

    Les temporaires de rendu restes en plan sont ranges d'abord : la purge
    retire ensuite le verrou et le dossier d'un clip disparu, dont le rangement
    a justement besoin (fix round 3, F3).
    """
    ranger_temporaires(conn, jobs_dir)
    if not jobs_dir.is_dir():
        return
    gardes: dict[str, int] = {}
    _purger_orphelins(conn, jobs_dir, "clip", "clips", CACHES_CLIP, (), (), gardes)
    _purger_orphelins(conn, jobs_dir, "vod", "vods", CACHES_VOD,
                      CACHES_VOD_MOTIFS, CACHES_VOD_DOSSIERS, gardes)
    if gardes:
        # Les rendus refuses ou non promus d'un clip supprime sont a nous : ils
        # restent (jamais de suppression), une ligne par demarrage suffit.
        logger.info("%d rendu(s) mis de cote garde(s) dans %s (%s) : a trier a la main",
                    sum(gardes.values()), jobs_dir,
                    ", ".join(f"{nom} : {n}" for nom, n in sorted(gardes.items())))
    for job_dir in jobs_dir.glob("vod*"):
        if _id_du_dossier(job_dir, "vod") is not None:
            for frame in job_dir.glob("frame*.png"):
                _retirer(frame)


def _purger_orphelins(conn: sqlite3.Connection, jobs_dir: Path, prefixe: str, table: str,
                      noms: tuple[str, ...], motifs: tuple[str, ...],
                      dossiers: tuple[str, ...], gardes: dict[str, int]) -> None:
    vivants = {r["id"] for r in conn.execute(f"SELECT id FROM {table}")}
    for job_dir in jobs_dir.glob(f"{prefixe}*"):
        if _lien(job_dir):
            # Une jonction jobs/clip9 vers un dossier quelconque faisait retirer
            # les caches nommes DANS LA CIBLE, hors de jobs/ (fix round 2, K5).
            logger.warning("Dossier de job %s ignore : lien ou jonction vers %s, la purge ne "
                           "suit pas les liens", job_dir.name, os.path.realpath(job_dir))
            continue
        ident = _id_du_dossier(job_dir, prefixe)
        if ident is None or ident in vivants:
            continue
        for nom in noms:
            _retirer(job_dir / nom)
        for motif in motifs:
            for cible in job_dir.glob(motif):
                _retirer(cible)
        for nom in dossiers:
            _retirer_arbre(job_dir / nom)
        restes = sorted(p.name for p in job_dir.iterdir())
        etrangers = [nom for nom in restes if not MIS_DE_COTE.fullmatch(nom)]
        if len(etrangers) < len(restes):
            gardes[job_dir.name] = len(restes) - len(etrangers)
        if etrangers:
            logger.warning("Dossier de job %s garde : %s n'est pas a nous",
                           job_dir.name, ", ".join(etrangers))
        if not restes:
            _retirer_dossier_vide(job_dir)


def _id_du_dossier(job_dir: Path, prefixe: str) -> int | None:
    """id du dossier jobs/<prefixe><id>, ou None si ce n'en est pas un.

    isascii() en plus de isdigit() : « clip² » passait le filtre (isdigit est
    vrai des chiffres unicode) puis faisait lever ValueError au demarrage.
    Forme canonique seulement : « clip01 » ou « clip0000000000000000002 »
    n'ont jamais ete ecrits par ClipForge, ils etaient pris pour des ids.
    """
    suffixe = job_dir.name.removeprefix(prefixe)
    if not (suffixe.isascii() and suffixe.isdigit()) or str(int(suffixe)) != suffixe:
        return None
    return None if _lien(job_dir) or not job_dir.is_dir() else int(suffixe)


def _lien(chemin: Path) -> bool:
    return chemin.is_symlink() or os.path.isjunction(chemin)


def _retirer(cible: Path) -> None:
    try:
        cible.unlink(missing_ok=True)
    except OSError as exc:
        logger.warning("Cache %s non supprime : %s", cible, exc)


def _retirer_dossier_vide(job_dir: Path) -> None:
    try:
        job_dir.rmdir()
    except OSError as exc:
        logger.warning("Dossier de job %s non supprime : %s", job_dir, exc)


def _retirer_arbre(cible: Path) -> None:
    if not cible.is_dir():
        return
    shutil.rmtree(cible, onexc=lambda _f, chemin, exc: logger.warning(
        "Cache %s non supprime : %s", chemin, exc))
