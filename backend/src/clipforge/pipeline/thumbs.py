import logging
import subprocess
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

THUMB_WIDTH = 480
CANDIDATE_LEAD_S = 1.0
VOD_THUMB_RATIO = 0.1
# Une vignette de 480 px se rend en une seconde sur une source indexee. Trente
# secondes bornent le cas mesure le 2026-08-30 : `-ss 1717` sur un MPEG-TS de
# 19,5 Go sans index degenere en balayage lineaire, et trois ffmpeg tournaient
# encore APRES l'arret du moteur (4 min, 2,1 min, 1,8 min d'age). Ce delai borne
# la vignette qui traine PENDANT que le moteur tourne ; l'orphelin qui lui
# survivait est mort avec lui depuis que clipforge/winjob.py assigne le moteur a
# un Job Object — ce n'est plus une fenetre de 30 s a tolerer.
THUMB_TIMEOUT_S = 30.0
# Ce qu'un second appelant attend le travail du premier avant de se replier sur
# l'affichage sans vignette. Court : la page ne doit pas retenir une requete.
THUMB_WAIT_S = 5.0

# Une generation en cours par vignette, partagee par tout le moteur : l'API tourne
# dans un threadpool, et chaque chargement de la page redemandait le meme instant.
_verrou = threading.Lock()
_en_cours: dict[Path, threading.Event] = {}


class ThumbError(RuntimeError):
    pass


def thumb_path(jobs_dir: Path, vod_id: int, t_s: float) -> Path:
    """Chemin de la vignette, quantifie au dixieme de seconde (cle du cache disque)."""
    return jobs_dir / f"vod{vod_id}" / "thumbs" / f"t{t_s:.1f}.jpg"


def _reserver(out: Path) -> tuple[threading.Event, bool]:
    """Prend le travail si personne ne l'a. Rend (temoin de fin, a moi de le faire)."""
    with _verrou:
        fini = _en_cours.get(out)
        if fini is not None:
            return fini, False
        fini = threading.Event()
        _en_cours[out] = fini
        return fini, True


def _liberer(out: Path, fini: threading.Event) -> None:
    """Rend le travail et reveille les attendeurs, que la generation ait tenu ou non."""
    with _verrou:
        _en_cours.pop(out, None)
    fini.set()


def _generer(out: Path, source: Path, quantized: float) -> Path:
    """Un ffmpeg, borne dans le temps, ecrit en deux temps.

    Le .tmp puis replace : une vignette presente dans le cache est toujours
    complete, meme si ffmpeg meurt en cours. L'expiration du delai TUE le
    processus (subprocess.run le fait avant de relever) et laisse l'appelant se
    replier sur l'affichage sans vignette.
    """
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(f"{out.stem}.tmp.jpg")
    try:
        proc = subprocess.run(
            ["ffmpeg", "-y", "-ss", str(quantized), "-i", str(source),
             "-vf", f"scale={THUMB_WIDTH}:-2", "-frames:v", "1", "-q:v", "4", str(tmp)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            check=False, timeout=THUMB_TIMEOUT_S)
    except subprocess.TimeoutExpired as exc:
        tmp.unlink(missing_ok=True)
        raise ThumbError(
            f"Vignette a {quantized}s abandonnee apres {THUMB_TIMEOUT_S:.0f}s "
            "(source non indexee ?)") from exc
    except OSError as exc:
        raise ThumbError(
            "ffmpeg introuvable — installer ffmpeg (winget install Gyan.FFmpeg)") from exc
    if proc.returncode != 0 or not tmp.is_file():
        tmp.unlink(missing_ok=True)
        raise ThumbError(f"Vignette impossible a {quantized}s : {proc.stderr[-200:]}")
    tmp.replace(out)
    return out


def ensure_thumb(jobs_dir: Path, vod_id: int, source: Path, t_s: float) -> Path:
    """Vignette JPEG a l'instant t, generee une seule fois puis relue du cache.

    Une seule generation a la fois par vignette : sur une source non indexee,
    chaque visite de la page ajoutait un ffmpeg de plusieurs minutes sur le meme
    instant (trois orphelins mesures le 2026-08-30). Le second appelant attend
    brievement le premier, puis se replie plutot que de retenir la requete.
    """
    quantized = round(t_s, 1)
    out = thumb_path(jobs_dir, vod_id, quantized)
    if out.is_file():
        return out
    fini, a_moi = _reserver(out)
    if not a_moi:
        if fini.wait(THUMB_WAIT_S) and out.is_file():
            return out
        raise ThumbError(f"Vignette a {quantized}s deja en cours de generation")
    try:
        return _generer(out, source, quantized)
    finally:
        _liberer(out, fini)


def try_thumb(jobs_dir: Path, vod_id: int, source: Path, t_s: float) -> Path | None:
    """Variante best-effort pour le pipeline : l'echec est journalise, jamais propage.

    Une vignette manquante degrade l'affichage ; elle ne doit pas faire echouer
    une analyse de plusieurs heures. L'endpoint de service la regenerera.
    """
    try:
        return ensure_thumb(jobs_dir, vod_id, source, t_s)
    except (ThumbError, OSError) as exc:
        logger.warning("Vignette vod %s a %.1fs impossible : %s", vod_id, t_s, exc)
        return None
