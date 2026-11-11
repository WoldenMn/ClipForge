import asyncio
import json
import logging
import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, ValidationError, field_validator

from clipforge import config, usage
from clipforge import db as dbmod
from clipforge.api.deps import get_conn
from clipforge.models import SubtitleStyle
from clipforge.pipeline.runner import STEP_ORDER
from clipforge.render.queue import render_clip_job

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/clips", tags=["clips"])

WORD_KEYS = {"start", "end", "text"}


class ClipCreate(BaseModel):
    segment_id: int


class ClipPatch(BaseModel):
    start_s: float | None = Field(default=None, ge=0)
    end_s: float | None = None
    title: str | None = None
    profile_id: int | None = None
    subtitle_style: str | None = None
    words_json: str | None = Field(default=None, max_length=2_000_000)

    @field_validator("subtitle_style")
    @classmethod
    def _validate_subtitle_style(cls, v: str | None) -> str | None:
        if v is None:
            return v
        try:
            SubtitleStyle(**json.loads(v))
        except (json.JSONDecodeError, ValidationError, TypeError) as exc:
            raise ValueError(f"subtitle_style invalide : {exc}") from exc
        return v

    @field_validator("words_json")
    @classmethod
    def _validate_words_json(cls, v: str | None) -> str | None:
        if v is None:
            return v
        try:
            data = json.loads(v)
        except json.JSONDecodeError as exc:
            raise ValueError(f"words_json invalide : {exc}") from exc
        if not isinstance(data, list):
            raise TypeError("words_json doit etre une liste de mots")
        for word in data:
            if not isinstance(word, dict) or set(word.keys()) != WORD_KEYS:
                raise ValueError(
                    "words_json : chaque mot doit avoir exactement les cles start/end/text")
            start, end, text = word["start"], word["end"], word["text"]
            if isinstance(start, bool) or isinstance(end, bool) \
                    or not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
                raise TypeError("words_json : start/end doivent etre des nombres")
            if not isinstance(text, str):
                raise TypeError("words_json : text doit etre une chaine")
        return v


PATCH_FIELD_QUERIES = {
    "start_s": "UPDATE clips SET start_s=? WHERE id=?",
    "end_s": "UPDATE clips SET end_s=? WHERE id=?",
    "title": "UPDATE clips SET title=? WHERE id=?",
    "profile_id": "UPDATE clips SET profile_id=? WHERE id=?",
    "subtitle_style": "UPDATE clips SET subtitle_style=? WHERE id=?",
    "words_json": "UPDATE clips SET words_json=? WHERE id=?",
}


@router.post("")
def create_clip(body: ClipCreate, conn: sqlite3.Connection = Depends(get_conn)):
    """Cree un clip a partir d'un candidat, avec ses defauts de scene et de style.

    Le style par defaut est appose ICI plutot que lu au rendu : un clip garde
    ainsi le prereglage tel qu'il etait le jour de sa creation, et changer le
    prereglage ne reecrit pas sous le nez de l'utilisateur des clips qu'il a
    deja regles. Reglage vide (le defaut) = colonne NULL, comme avant. Le profil
    de scene suit la meme regle : resolu a la creation, jamais reecrit ensuite.
    """
    seg = conn.execute("SELECT * FROM segments WHERE id=?", (body.segment_id,)).fetchone()
    if not seg:
        raise HTTPException(404, "Segment inconnu")
    style = dbmod.get_setting(conn, "subtitle_style_default") or None
    cur = conn.execute(
        "INSERT INTO clips (segment_id, vod_id, start_s, end_s, title, subtitle_style, "
        "profile_id) VALUES (?,?,?,?,?,?,?)",
        (seg["id"], seg["vod_id"], seg["start_s"], seg["end_s"],
         seg["ai_title"] or "", style, dbmod.clip_profile_id(conn, seg["vod_id"])))
    conn.commit()
    usage.record(conn, "clip_created", vod_id=seg["vod_id"], segment_id=seg["id"],
                 rank=seg["rank"], top_signal=seg["top_signal"])
    return dict(conn.execute("SELECT * FROM clips WHERE id=?", (cur.lastrowid,)).fetchone())


@router.get("")
def list_clips(conn: sqlite3.Connection = Depends(get_conn)):
    return [dict(r) for r in conn.execute(
        "SELECT * FROM clips ORDER BY id DESC").fetchall()]


@router.get("/{clip_id}")
def get_clip(clip_id: int, conn: sqlite3.Connection = Depends(get_conn)):
    row = conn.execute("SELECT * FROM clips WHERE id=?", (clip_id,)).fetchone()
    if not row:
        raise HTTPException(404)
    return dict(row)


def _record_bound_shift(conn: sqlite3.Connection, clip: sqlite3.Row, fields: dict) -> None:
    """Decide si ce PATCH est un geste, et laisse usage.py le mesurer.

    Deux questions a ne pas confondre. « Ce patch a-t-il deplace une borne ? »
    est une question sur la REQUETE, elle se tranche ici, contre les bornes
    ACTUELLES du clip : l'editeur renvoie les bornes courantes a chaque sortie
    de champ, meme sans frappe, et ces PATCH inertes ne sont pas des gestes.
    « De combien ? » est une question sur la MESURE : elle se lit contre le
    candidat d'origine, dans usage.py, avec la lecture qu'elle demande sous la
    meme garde que l'ecriture.
    """
    if not clip["segment_id"] or not ("start_s" in fields or "end_s" in fields):
        return
    debut = fields.get("start_s", clip["start_s"])
    fin = fields.get("end_s", clip["end_s"])
    if debut == clip["start_s"] and fin == clip["end_s"]:
        return
    usage.record_bound_shift(conn, vod_id=clip["vod_id"], segment_id=clip["segment_id"],
                             start_s=debut, end_s=fin)


@router.patch("/{clip_id}")
def patch_clip(clip_id: int, body: ClipPatch, conn: sqlite3.Connection = Depends(get_conn)):
    row = conn.execute("SELECT * FROM clips WHERE id=?", (clip_id,)).fetchone()
    if not row:
        raise HTTPException(404)
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    if "start_s" in fields or "end_s" in fields:
        start = fields.get("start_s", row["start_s"])
        end = fields.get("end_s", row["end_s"])
        if end <= start:
            raise HTTPException(400, "Bornes invalides : end_s doit etre > start_s")
    for k, v in fields.items():
        conn.execute(PATCH_FIELD_QUERIES[k], (v, clip_id))
    if fields:
        conn.commit()
    _record_bound_shift(conn, row, fields)
    return dict(conn.execute("SELECT * FROM clips WHERE id=?", (clip_id,)).fetchone())


def _log_pool_failure(clip_id: int, fut) -> None:
    exc = fut.exception()
    if exc is not None:
        logger.error("Rendu clip %s mort au niveau process : %s", clip_id, exc)
        conn = dbmod.get_db_light(config.DB_PATH)
        try:
            conn.execute("UPDATE clips SET status='error', error=? WHERE id=?",
                         (f"process: {exc}", clip_id))
            conn.commit()
        finally:
            conn.close()


@router.post("/{clip_id}/render")
async def render(clip_id: int, request: Request,
                 conn: sqlite3.Connection = Depends(get_conn)):
    row = conn.execute("SELECT * FROM clips WHERE id=?", (clip_id,)).fetchone()
    if not row:
        raise HTTPException(404)
    if row["profile_id"] is None:
        raise HTTPException(400, "Associer un profil de scene avant le rendu")
    vod = dbmod.get_vod(conn, row["vod_id"])
    if vod and vod["status"] in STEP_ORDER:
        raise HTTPException(409, "Analyse en cours sur cette VOD")
    rendering = request.app.state.rendering
    fut = rendering.get(clip_id)
    if fut and not fut.done():
        raise HTTPException(409, "Rendu deja en cours")
    # Un candidat exporte vaut UNE ligne, pas une par tentative : le clip n'a
    # jamais ete mis en file tant qu'il est 'draft'. Une relance apres echec
    # (« Tout relancer », ou un clip bascule en erreur par le redemarrage) et une
    # re-export apres retouche portent sur le meme candidat, deja consigne.
    premier_passage = row["status"] == "draft"
    conn.execute("UPDATE clips SET status='queued' WHERE id=?", (clip_id,))
    conn.commit()
    if premier_passage:
        usage.record_export(conn, vod_id=row["vod_id"], segment_id=row["segment_id"])
    loop = asyncio.get_running_loop()
    fut = loop.run_in_executor(request.app.state.pool, render_clip_job, clip_id,
                               str(config.DB_PATH), str(config.JOBS_DIR),
                               str(config.OUTPUT_DIR))
    fut.add_done_callback(lambda f: _log_pool_failure(clip_id, f))
    rendering[clip_id] = fut
    return {"queued": True}
