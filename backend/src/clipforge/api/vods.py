import asyncio
import logging
import mimetypes
import shutil
import sqlite3
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from fastapi.sse import EventSourceResponse, ServerSentEvent
from pydantic import BaseModel

from clipforge import config
from clipforge import db as dbmod
from clipforge.api.deps import get_conn
from clipforge.api.garde_analyse import refus_d_analyse
from clipforge.errors import user_message
from clipforge.pipeline import previews, thumbs
from clipforge.pipeline.download import twitch_vod_id
from clipforge.pipeline.runner import resume_step, run_analysis_pipeline

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/vods", tags=["vods"])

ALLOWED_EXTENSIONS = {".mp4", ".mkv", ".mov", ".ts", ".webm", ".avi"}


class LocalImport(BaseModel):
    path: str
    twitch_url: str | None = None


class TwitchImport(BaseModel):
    url: str


def _vod_out(row: sqlite3.Row) -> dict:
    return dict(row)


@router.post("/import-local")
def import_local(body: LocalImport, conn=Depends(get_conn)):
    p = Path(body.path)
    if not p.is_file():
        raise HTTPException(400, f"Fichier introuvable : {body.path}")
    if p.suffix.lower() not in ALLOWED_EXTENSIONS:
        raise HTTPException(400, f"Extension non supportee : {p.suffix}")
    vid = twitch_vod_id(body.twitch_url) if body.twitch_url else None
    vod_id = dbmod.create_vod(conn, title=p.stem, source_path=str(p),
                              twitch_url=body.twitch_url, twitch_vod_id=vid)
    return _vod_out(dbmod.get_vod(conn, vod_id))


@router.post("/import-twitch")
def import_twitch(body: TwitchImport, conn=Depends(get_conn)):
    vid = twitch_vod_id(body.url)
    if not vid:
        raise HTTPException(400, "URL de VOD Twitch invalide (attendu twitch.tv/videos/<id>)")
    vod_id = dbmod.create_vod(conn, title=f"VOD {vid}", twitch_url=body.url,
                              twitch_vod_id=vid)
    return _vod_out(dbmod.get_vod(conn, vod_id))


@router.get("")
def list_vods(conn=Depends(get_conn)):
    return [dict(r) for r in
            conn.execute("SELECT * FROM vods ORDER BY id DESC").fetchall()]


@router.get("/{vod_id}")
def get_vod(vod_id: int, conn=Depends(get_conn)):
    row = dbmod.get_vod(conn, vod_id)
    if not row:
        raise HTTPException(404)
    return _vod_out(row)


@router.delete("/{vod_id}")
def delete_vod(vod_id: int, conn=Depends(get_conn)):
    """Supprime la VOD, ses donnees derivees et ses fichiers de travail.

    Le fichier video source n'est JAMAIS supprime ici : son chemin et sa taille
    sont renvoyes pour que l'interface propose la suppression a part, apres
    confirmation. Plusieurs gigaoctets ne partent pas sur un simple clic.
    Mots, signaux et segments partent par cascade ON DELETE CASCADE.
    """
    row = dbmod.get_vod(conn, vod_id)
    if not row:
        raise HTTPException(404)
    source_path = row["source_path"] or ""
    size_mo = None
    if source_path and Path(source_path).is_file():
        size_mo = round(Path(source_path).stat().st_size / 1048576)
    conn.execute("DELETE FROM clips WHERE vod_id=?", (vod_id,))
    conn.execute("DELETE FROM vods WHERE id=?", (vod_id,))
    conn.commit()
    shutil.rmtree(config.JOBS_DIR / f"vod{vod_id}", ignore_errors=True)
    logger.info("Vod %s supprimee (source conservee : %s)", vod_id, source_path or "aucune")
    return {"deleted": True, "source_path": source_path, "source_size_mo": size_mo}


def _log_pool_failure(vod_id: int, fut) -> None:
    exc = fut.exception()
    if exc is not None:
        logger.error("Pipeline vod %s mort au niveau process : %s", vod_id, exc)
        conn = dbmod.get_db_light(config.DB_PATH)
        try:
            dbmod.set_vod_error(conn, vod_id, user_message(exc), f"process: {exc}")
            # Un worker mort pendant l'etape de montage laissait ses clips en
            # 'queued'/'rendering' pour toujours, et la garde d'analyse les
            # comptait : /analyze refusait a jamais un travail qui ne tournait
            # plus (re-revue du fix round 3).
            conn.execute("UPDATE clips SET status='error', error=? "
                         "WHERE vod_id=? AND status IN ('queued', 'rendering')",
                         ("Le montage s'est interrompu : relance le rendu de ce clip", vod_id))
            conn.commit()
        finally:
            conn.close()


@router.post("/{vod_id}/analyze")
async def analyze(vod_id: int, request: Request, conn=Depends(get_conn),
                  profile: str | None = Query(default=None, max_length=80)):
    """Lance l'analyse. `profile` nomme le profil de scene qui cadrera les clips
    de cette VOD (pont OBS) ; absent, la VOD garde le sien ou suit le reglage."""
    vod = dbmod.get_vod(conn, vod_id)
    if not vod:
        raise HTTPException(404)
    profile_id = None
    if profile is not None:
        row = conn.execute("SELECT id FROM scene_profiles WHERE name=?", (profile,)).fetchone()
        if not row:
            raise HTTPException(400, f"Profil de scene inconnu : {profile}")
        profile_id = row["id"]
    running = request.app.state.running
    if refus := refus_d_analyse(conn, running, vod_id):
        raise HTTPException(409, refus)
    # avant l'UPDATE : il efface les deux traces dont depend le point de reprise
    resume_from = resume_step(vod)
    conn.execute(
        f"UPDATE vods SET status='queued', progress=0, error=NULL, error_detail=NULL, "
        f"step_label=NULL, eta_s=NULL, profile_id=COALESCE(?, profile_id), "
        f"{dbmod.TOUCH_UPDATED_AT} WHERE id=?",
        (profile_id, vod_id))
    conn.commit()
    loop = asyncio.get_running_loop()
    fut = loop.run_in_executor(request.app.state.pool, run_analysis_pipeline,
                               vod_id, str(config.DB_PATH), resume_from)
    fut.add_done_callback(lambda f: _log_pool_failure(vod_id, f))
    running[vod_id] = fut
    return {"started": True}


def _read_vod_state(vod_id: int) -> dict | None:
    conn = dbmod.get_db_light(config.DB_PATH)
    try:
        row = dbmod.get_vod(conn, vod_id)
    finally:
        conn.close()
    return dict(row) if row else None


@router.get("/{vod_id}/events", response_class=EventSourceResponse)
async def events(vod_id: int):
    while True:
        row = await asyncio.to_thread(_read_vod_state, vod_id)
        state = row if row else {"status": "unknown"}
        yield ServerSentEvent(data=state, event="progress")
        if state.get("status") in ("ready", "error", "unknown"):
            break
        await asyncio.sleep(0.5)


@router.get("/{vod_id}/file")
def vod_file(vod_id: int, conn=Depends(get_conn)):
    row = dbmod.get_vod(conn, vod_id)
    if not row or not row["source_path"] or not Path(row["source_path"]).is_file():
        raise HTTPException(404)
    media_type = mimetypes.guess_type(row["source_path"])[0] or "video/mp4"
    return FileResponse(row["source_path"], media_type=media_type)


@router.get("/{vod_id}/words")
def vod_words(vod_id: int, start_s: float, end_s: float, conn=Depends(get_conn)):
    rows = conn.execute(
        "SELECT start_s AS start, end_s AS end, text FROM words "
        "WHERE vod_id=? AND start_s>=? AND end_s<=? ORDER BY idx",
        (vod_id, start_s, end_s)).fetchall()
    return [dict(r) for r in rows]


@router.get("/{vod_id}/thumb")
def vod_thumb(vod_id: int, t: float = Query(ge=0), conn=Depends(get_conn)):
    """Vignette JPEG a l'instant t : servie du cache, regeneree si absente."""
    row = dbmod.get_vod(conn, vod_id)
    if not row or not row["source_path"] or not Path(row["source_path"]).is_file():
        raise HTTPException(404)
    try:
        out = thumbs.ensure_thumb(config.JOBS_DIR, vod_id, Path(row["source_path"]), t)
    except thumbs.ThumbError as exc:
        raise HTTPException(404, str(exc)) from exc
    return FileResponse(out, media_type="image/jpeg")


@router.get("/{vod_id}/preview")
def vod_preview(vod_id: int, t: float = Query(ge=0), conn=Depends(get_conn)):
    """Extrait video de quelques secondes a l'instant t : servi du cache, sinon genere.

    Le pipeline en produit un par candidat ; cet endpoint couvre les VOD analysees
    avant l'arrivee des apercus et les instants qu'aucun candidat ne couvre.
    """
    row = dbmod.get_vod(conn, vod_id)
    if not row or not row["source_path"] or not Path(row["source_path"]).is_file():
        raise HTTPException(404)
    try:
        out = previews.ensure_preview(config.JOBS_DIR, vod_id, Path(row["source_path"]), t)
    except previews.PreviewError as exc:
        raise HTTPException(404, str(exc)) from exc
    return FileResponse(out, media_type="video/mp4")


@router.get("/{vod_id}/frame")
def vod_frame(vod_id: int, t: float = Query(ge=0), conn=Depends(get_conn)):
    """PNG d'une frame (pour tracer le profil de scene dans l'UI)."""
    import subprocess
    row = dbmod.get_vod(conn, vod_id)
    if not row or not row["source_path"] or not Path(row["source_path"]).is_file():
        raise HTTPException(404)
    out = config.JOBS_DIR / f"vod{vod_id}" / f"frame_{t:.1f}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    if not out.is_file():
        tmp = out.with_name(f"frame_{t:.1f}.tmp.png")
        proc = subprocess.run(
            ["ffmpeg", "-y", "-ss", str(t), "-i", row["source_path"],
             "-vf", "scale=480:-1", "-frames:v", "1", "-update", "1", str(tmp)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            check=False)
        if proc.returncode != 0:
            tmp.unlink(missing_ok=True)
            raise HTTPException(500, f"Extraction frame echouee : {proc.stderr[-300:]}")
        if not tmp.is_file():
            raise HTTPException(404, "Frame hors duree")
        tmp.replace(out)
    return FileResponse(out, media_type="image/png")
