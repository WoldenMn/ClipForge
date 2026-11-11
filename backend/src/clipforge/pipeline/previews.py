import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

PREVIEW_WIDTH = 480
PREVIEW_DURATION_S = 3.0
# Un instant hors duree fait sortir ffmpeg en SUCCES avec un conteneur vide.
# Mesure sur la fixture : 261 octets a vide, ~50 Ko pour trois secondes reelles.
# Le seuil tranche entre les deux, loin de l'un comme de l'autre.
MIN_PREVIEW_BYTES = 2048


class PreviewError(RuntimeError):
    pass


def preview_path(jobs_dir: Path, vod_id: int, t_s: float) -> Path:
    """Chemin de l'extrait, quantifie au dixieme de seconde (cle du cache disque)."""
    return jobs_dir / f"vod{vod_id}" / "previews" / f"t{t_s:.1f}.mp4"


def ensure_preview(jobs_dir: Path, vod_id: int, source: Path, t_s: float) -> Path:
    """Extrait muet de PREVIEW_DURATION_S a l'instant t, genere une fois puis relu du cache.

    Reencode en PREVIEW_WIDTH plutot que de copier le flux : sur une source non
    indexee, les deux coutent le meme temps (le prix est l'ouverture du conteneur,
    pas l'encodage de trois secondes) mais la copie pese quelques megaoctets quand
    le reencodage tient en quelques dizaines de kilooctets. Piste audio jetee : la
    carte lit l'apercu en muet.

    Ecriture en deux temps (.tmp puis replace) : un extrait present dans le cache
    est donc toujours complet, meme si ffmpeg meurt en cours.
    """
    quantized = round(t_s, 1)
    out = preview_path(jobs_dir, vod_id, quantized)
    if out.is_file():
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(f"{out.stem}.tmp.mp4")
    try:
        proc = subprocess.run(
            ["ffmpeg", "-y", "-ss", str(quantized), "-i", str(source),
             "-t", str(PREVIEW_DURATION_S), "-vf", f"scale={PREVIEW_WIDTH}:-2", "-an",
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
             "-movflags", "+faststart", str(tmp)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    except OSError as exc:
        raise PreviewError(
            "ffmpeg introuvable — installer ffmpeg (winget install Gyan.FFmpeg)") from exc
    if proc.returncode != 0 or not tmp.is_file():
        tmp.unlink(missing_ok=True)
        raise PreviewError(f"Extrait impossible a {quantized}s : {proc.stderr[-200:]}")
    if tmp.stat().st_size < MIN_PREVIEW_BYTES:
        tmp.unlink(missing_ok=True)
        raise PreviewError(f"Aucune image a {quantized}s : instant hors de la video")
    tmp.replace(out)
    return out


def try_preview(jobs_dir: Path, vod_id: int, source: Path, t_s: float) -> Path | None:
    """Variante best-effort pour le pipeline : l'echec est journalise, jamais propage.

    Un apercu manquant degrade l'affichage ; il ne doit pas faire echouer une
    analyse de plusieurs heures. L'endpoint de service le regenerera.
    """
    try:
        return ensure_preview(jobs_dir, vod_id, source, t_s)
    except (PreviewError, OSError) as exc:
        logger.warning("Apercu vod %s a %.1fs impossible : %s", vod_id, t_s, exc)
        return None
