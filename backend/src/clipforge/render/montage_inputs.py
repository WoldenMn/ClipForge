"""Entrees du montage lues en base, et leurs replis : mots du clip, serie audio
de la VOD, cadence de la source, emoji d'accroche.

Extrait de render/montage_plan.py (revue finale V6, lot C) : l'orchestrateur y
garde les decisions de montage, ce module ne fait que lire.
"""
import json
import logging
import sqlite3
from pathlib import Path

from clipforge import db as dbmod
from clipforge.pipeline.segmenter import Word
from clipforge.render.captions_pop import HOOK_MAX_S
from clipforge.render.sfx import emoji_path

logger = logging.getLogger(__name__)

DEFAULT_FPS = 24.0


def clip_words(conn: sqlite3.Connection, clip: sqlite3.Row) -> list[Word]:
    """Mots du clip, bornes a son intervalle.

    Un groupe pop ne clampe QUE sa fin (captions_pop.py) : un mot commencant
    apres la fin du clip donnerait une ligne qui n'a nulle part ou tenir. La
    requete SQL borne deja, mais le words_json edite par l'utilisateur n'est
    borne par personne — d'ou le filtre commun aux deux sources.
    """
    if clip["words_json"]:
        mots = [Word(**w) for w in json.loads(clip["words_json"])]
    else:
        mots = [Word(start=r["start_s"], end=r["end_s"], text=r["text"])
                for r in conn.execute(
                    "SELECT * FROM words WHERE vod_id=? AND start_s>=? AND end_s<=? "
                    "ORDER BY idx", (clip["vod_id"], clip["start_s"], clip["end_s"]))]
    return [w for w in mots if clip["start_s"] <= w.start and w.end <= clip["end_s"]]


def audio_series(conn: sqlite3.Connection, vod_id: int) -> list[tuple[float, float]]:
    return [(r["t_s"], r["score"]) for r in conn.execute(
        "SELECT t_s, score FROM signals WHERE vod_id=? AND kind='audio' ORDER BY t_s",
        (vod_id,))]


def source_fps(vod: sqlite3.Row) -> float:
    """Cadence de la source. En V6, toute la video du clip est reechantillonnee
    sur sa grille (render.frame_grid), zoompan compris ; en V5, zoompan
    seul la suit. Une cadence fausse ne desynchronise rien mais saccade tout."""
    fps = vod["fps"]
    if fps and fps > 0:
        return float(fps)
    logger.warning("VOD %s sans fps exploitable (%r) : toute la video du clip est "
                   "reechantillonnee a %s ips en V6 (zoompan seul en V5)",
                   vod["id"], fps, DEFAULT_FPS)
    return DEFAULT_FPS


def emoji_overlay(conn: sqlite3.Connection, clip: sqlite3.Row,
                  dur_s: float) -> tuple[Path, float, float] | None:
    """PNG de l'emoji d'accroche et sa fenetre, ou None : reglage coupe, clip sans
    emoji, ou caractere hors du jeu vendorise (aucun telechargement au rendu)."""
    if dbmod.get_setting(conn, "captions_emojis") != "1":
        return None
    char = (clip["emoji"] or "").strip()
    if not char:
        return None
    png = emoji_path(char)
    return None if png is None else (png, 0.0, min(HOOK_MAX_S, dur_s))
