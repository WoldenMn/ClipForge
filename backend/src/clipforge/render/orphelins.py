"""Au demarrage : les temporaires de rendu restes en plan sont ranges, jamais supprimes.

Un rendu tue pendant l'ecriture laisse clipforge-c<id>.part.mp4 dans le dossier
de livraison, ou son nom d'attente .ecarte. La retouche suivante du clip le
range ; mais un clip supprime entre-temps n'a plus de retouche, et le temporaire
restait la pour toujours (revue finale V6, fix round 3, F3).

Ou chercher : le dossier de sortie du reglage, celui de la configuration, et
celui que le verrou de chaque clip a note a son dernier rendu -- le reglage a pu
changer depuis, et un premier rendu interrompu n'a pas de livrable pour dire ou
il ecrivait.
"""
import logging
import os
import re
import sqlite3
from pathlib import Path

from clipforge import db as dbmod
from clipforge.render.livraison import ecarter
from clipforge.render.verrou import RenduDejaEnCours, temporaire_note, verrou_du_clip

logger = logging.getLogger(__name__)

TEMPORAIRE = re.compile(r"(?a)clipforge-c(\d+)\.part\.mp4(\.ecarte)?")
POUBELLE = "_poubelle"


def _lien(chemin: Path) -> bool:
    """Un lien ou une jonction mene ou il veut : ni la purge (fix round 2, K5)
    ni le rangement ne le suivent, ni pour lire ni pour ecrire."""
    return chemin.is_symlink() or os.path.isjunction(chemin)


def ranger_temporaires(conn: sqlite3.Connection, jobs_dir: Path,
                       output_dir: Path | str | None = None) -> None:
    """Range les temporaires dont aucun rendu ne se sert plus.

    Celui d'un clip encore la part dans son dossier de job, celui d'un clip
    supprime dans le `_poubelle` du dossier des jobs : la purge entretient les
    dossiers de job des clips vivants, et les restes d'un clip mort n'ont rien a
    y faire (`clips.id` est AUTOINCREMENT, il n'est jamais reattribue).
    Un clip dont le verrou est tenu est laisse tel quel : son rendu vit encore.

    Un dossier illisible (droits, partage reseau, dossier cloud en mauvais etat)
    est journalise et passe : une etape de menage n'empeche pas le demarrage.
    """
    from clipforge import config
    vivants = {r["id"] for r in conn.execute("SELECT id FROM clips")}
    for dossier in _dossiers_de_sortie(conn, jobs_dir, output_dir or config.OUTPUT_DIR):
        try:
            chemins = sorted(dossier.iterdir())
        except OSError as exc:
            logger.warning("Dossier %s non inspecte (%s) : ses temporaires restent en place",
                           dossier, exc.strerror)
            continue
        for chemin in chemins:
            trouve = TEMPORAIRE.fullmatch(chemin.name)
            # Id canonique seulement : clipforge-c007 n'a jamais ete ecrit par
            # ClipForge, et le ranger dans le job du clip 7 le ferait passer pour
            # un de ses artefacts (meme garde que job_cache._id_du_dossier).
            if trouve and str(int(trouve[1])) == trouve[1] and chemin.is_file():
                _ranger(chemin, int(trouve[1]), vivants, jobs_dir)


def _dossiers_de_sortie(conn: sqlite3.Connection, jobs_dir: Path,
                        output_dir: Path | str) -> list[Path]:
    """Dossiers ou un temporaire peut trainer, dedoublonnes, existants seulement."""
    dossiers = [Path(dbmod.get_setting(conn, "output_dir") or output_dir), Path(output_dir)]
    for job_dir in sorted(jobs_dir.glob("clip*")) if jobs_dir.is_dir() else []:
        if _lien(job_dir):
            continue
        note = temporaire_note(job_dir)
        if note is not None:
            dossiers.append(note.parent)
    vus, retenus = set(), []
    for dossier in dossiers:
        cle = os.path.normcase(os.path.abspath(dossier))
        if cle not in vus and dossier.is_dir():
            vus.add(cle)
            retenus.append(dossier)
    return retenus


def _ranger(part: Path, clip_id: int, vivants: set[int], jobs_dir: Path) -> None:
    job_dir = jobs_dir / f"clip{clip_id}"
    cible, motif = ((job_dir, "orphelin") if clip_id in vivants
                    else (jobs_dir / POUBELLE, f"clip{clip_id}-orphelin"))
    if _lien(job_dir) or _lien(cible):
        logger.warning("Temporaire %s laisse en place : %s est un lien", part, cible)
        return
    try:
        with verrou_du_clip(job_dir):
            garde = ecarter(part, cible, motif)
    except RenduDejaEnCours:
        logger.info("Temporaire %s laisse en place : un rendu de ce clip est en cours", part)
        return
    except OSError as exc:
        logger.warning("Temporaire %s non range (%s) : il reste dans %s",
                       part, exc.strerror, part.parent)
        return
    logger.warning("Temporaire d'un rendu interrompu (%s) range : %s", part, garde)
