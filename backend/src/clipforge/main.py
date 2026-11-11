import json
import logging
import os
import sqlite3
import subprocess
import sys
import threading
from concurrent.futures import ProcessPoolExecutor
from contextlib import asynccontextmanager
from multiprocessing import get_context
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from clipforge import config, winjob
from clipforge import db as dbmod
from clipforge.api.clips import router as clips_router
from clipforge.api.clips_auto import router as clips_auto_router
from clipforge.api.deps import get_conn
from clipforge.api.diagnostic import router as diagnostic_router
from clipforge.api.health import router as health_router
from clipforge.api.profiles import router as profiles_router
from clipforge.api.segments import router as segments_router
from clipforge.api.settings import router as settings_router
from clipforge.api.vods import router as vods_router
from clipforge.job_cache import purge_stale_job_caches
from clipforge.pipeline.runner import STEP_ORDER

config.setup_logging()
config.ensure_dirs()

logger = logging.getLogger(__name__)


def _reconcile_orphan_statuses(conn: sqlite3.Connection) -> None:
    """Marque en erreur les jobs restes 'en cours' apres un arret du serveur.

    Les statuts intermediaires (queued + etapes du pipeline, queued/rendering
    pour les clips) ne peuvent pas survivre a un redemarrage : le pool de
    process est perdu, ces jobs ne finiront jamais seuls.

    error_detail est reecrit en meme temps que error, avec l'etape interrompue :
    le laisser tel quel afficherait le detail technique d'un echec anterieur a
    cote d'un message qui n'a rien a voir.
    """
    interrupted = ("queued", *STEP_ORDER)
    placeholders = ",".join("?" for _ in interrupted)
    conn.execute(
        f"UPDATE vods SET status='error', error=?, "
        f"error_detail=status||': interrompu par redemarrage', {dbmod.TOUCH_UPDATED_AT} "
        f"WHERE status IN ({placeholders})",
        ("Analyse interrompue par le redemarrage de l'application. Relance-la pour reprendre.",
         *interrupted))
    conn.execute(
        "UPDATE clips SET status='error', error='interrompu par redemarrage' "
        "WHERE status IN ('queued','rendering')")
    conn.commit()


def _warmup_gpu() -> None:
    """Charge le modele et fait une inference a vide, hors du chemin utilisateur.

    Sur GPU Blackwell, la compilation JIT des kernels peut prendre des dizaines de
    minutes au tout premier appel ; la faire ici la sort du chemin utilisateur et
    le driver la met en cache pour toujours.

    CLIPFORGE_NO_WARMUP coupe ce prechauffage : il reserve plusieurs gigaoctets
    des le demarrage, ce qui n'a aucun sens quand l'application est lancee en
    serie (suite de tests) ou sur une machine juste assez dotee. Le premier
    transcodage paie alors le JIT lui-meme.
    """
    if os.environ.get("CLIPFORGE_NO_WARMUP"):
        logger.info("Warmup GPU desactive par CLIPFORGE_NO_WARMUP")
        return

    import numpy as np

    from clipforge import db as dbmod
    from clipforge.gpu import register_cuda_dlls
    try:
        register_cuda_dlls()
        from faster_whisper import WhisperModel
        conn = dbmod.get_db_light(config.DB_PATH)
        try:
            model_name = (os.environ.get("CLIPFORGE_WHISPER_MODEL")
                          or dbmod.get_setting(conn, "whisper_model") or "large-v3")
        finally:
            conn.close()
        from clipforge.gpu_lease import bail_gpu

        # Le warmup reserve plusieurs Go hors de toute file : il prend un bail
        # comme une vraie transcription, et le rend des que le modele est libere.
        with bail_gpu(model_name):
            model = WhisperModel(model_name, device="cuda",
                                 device_index=config.WHISPER_GPU_INDEX, compute_type="float16")
            segments, _ = model.transcribe(np.zeros(16000, dtype=np.float32), language="fr")
            list(segments)
            del model
        logger.info("Warmup GPU termine (%s)", model_name)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Warmup GPU ignore : %s", exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # AVANT le pool, et avant tout ffmpeg : un processus cree avant l'assignation
    # n'entre pas dans le job et survivrait au moteur. Ne leve jamais — le detail
    # d'un echec est dans winjob.py.
    winjob.bind_children_to_engine()
    conn = dbmod.get_db(config.DB_PATH)
    try:
        purge_stale_job_caches(conn, config.JOBS_DIR)
        _reconcile_orphan_statuses(conn)
    finally:
        conn.close()
    app.state.pool = ProcessPoolExecutor(max_workers=2, mp_context=get_context("spawn"))
    app.state.running = {}
    app.state.rendering = {}
    warmup = threading.Thread(target=_warmup_gpu, daemon=True, name="warmup-gpu")
    warmup.start()
    app.state.warmup = warmup
    yield
    app.state.pool.shutdown(cancel_futures=True)
    app.state.warmup.join(timeout=1.0)


app = FastAPI(title="ClipForge", lifespan=lifespan)

ALLOWED_HOSTS = {"127.0.0.1:8765", "localhost:8765"}
ALLOWED_ORIGINS = {"http://127.0.0.1:8765", "http://localhost:8765",
                   "http://localhost:5173", "http://127.0.0.1:5173"}  # dev Vite


@app.middleware("http")
async def check_host_origin(request: Request, call_next):
    """Anti DNS-rebinding / CSRF : serveur local sans auth, on refuse tout
    Host inattendu et toute requete cross-origin hors dev Vite."""
    host = request.headers.get("host", "")
    if host not in ALLOWED_HOSTS:
        return JSONResponse({"detail": "Host non autorise"}, status_code=403)
    origin = request.headers.get("origin")
    if origin and origin not in ALLOWED_ORIGINS:
        return JSONResponse({"detail": "Origin non autorisee"}, status_code=403)
    return await call_next(request)


app.include_router(vods_router)
app.include_router(segments_router)
app.include_router(clips_router)
app.include_router(clips_auto_router)
app.include_router(profiles_router)
app.include_router(settings_router)
app.include_router(diagnostic_router)
app.include_router(health_router)

PICKER_SCRIPT = """
import json, tkinter as tk
from tkinter import filedialog
root = tk.Tk(); root.withdraw(); root.attributes("-topmost", True)
path = filedialog.askopenfilename(title="Choisir une video",
    filetypes=[("Videos", "*.mp4 *.mkv *.mov *.ts *.webm"), ("Tous", "*.*")])
print(json.dumps(path))
"""


@app.post("/api/pick-file")
def pick_file():
    try:
        result = subprocess.run([sys.executable, "-c", PICKER_SCRIPT],
                                capture_output=True, text=True, timeout=300, check=False)
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(400, "Selection expiree (dialog reste ouvert)") from exc
    if result.returncode != 0:
        raise HTTPException(500, f"Picker echoue : {result.stderr[-300:]}")
    try:
        path = json.loads(result.stdout.strip() or '""')
    except json.JSONDecodeError as exc:
        raise HTTPException(500, "Sortie picker invalide") from exc
    if not path:
        raise HTTPException(400, "Selection annulee")
    return {"path": path}


@app.post("/api/open-output")
def open_output(conn: sqlite3.Connection = Depends(get_conn)):
    custom = dbmod.get_setting(conn, "output_dir")
    target = custom or str(config.OUTPUT_DIR)
    if not Path(target).is_dir():
        raise HTTPException(400, f"Dossier de sortie introuvable : {target}")
    os.startfile(target)  # explorer Windows
    return {"ok": True}


@app.get("/api/health")
async def health():
    return {"ok": True}


if config.FRONTEND_DIST.is_dir():
    app.frontend("/", directory=str(config.FRONTEND_DIST), fallback="index.html")
