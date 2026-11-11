import json
import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# Une sonde bloquee bloque tout ce qui l'attend : le rendu d'un clip, l'analyse
# d'une VOD. Mesure (ffprobe 9.0.1) : 71 a 226 ms sur les rendus reels de
# 16 a 39 Mo, 123 a 215 ms sur un MP4 de 981 Mo (la duree ne suit pas la
# taille, ffprobe lit l'index), 643 ms au pire, cache froid. 60 s laisse
# 93 fois le pire mesure -- de quoi couvrir un index a reconstruire sur un
# disque lent, jamais de quoi attendre une sonde qui ne reviendra pas.
PROBE_TIMEOUT_S = 60


@dataclass
class MediaInfo:
    """Debuts et fins de flux comptes depuis le debut du FICHIER
    (format.start_time), l'origine de -ss et de format.duration : un
    conteneur qui demarre a 1,479 s, video de 1,5 a 28,0 s, a une video qui
    finit a 26,521 s (revue finale V6, fix round 3, F2). Ce n'est pas
    l'origine des PTS : un MPEG-TS dont le conteneur ET l'audio demarrent a
    1,4 s a un audio qui part de 0,0, alors que son PTS absolu vaut 1,4."""
    duration_s: float
    width: int
    height: int
    fps: float
    video_duration_s: float | None = None
    audio_duration_s: float | None = None
    video_start_s: float = 0.0
    audio_start_s: float = 0.0
    # Un flux audio EXISTE : ffprobe ne donne pas de duree par flux pour un MKV
    # ou un WebM (format par defaut d'OBS), lire la duree ne repond donc pas a
    # la question (revue finale V6, fix round 3, F3).
    audio_present: bool = False

    @property
    def video_end_s(self) -> float | None:
        return _fin(self.video_start_s, self.video_duration_s)

    @property
    def audio_end_s(self) -> float | None:
        """Debut + duree. Un audio decale (debut 2,98 s, 27,02 s de duree)
        finit a 30,0 s, pas a 27,02 (revue finale V6, fix round 2, K4)."""
        return _fin(self.audio_start_s, self.audio_duration_s)


def _fin(debut: float, duree: float | None) -> float | None:
    return None if duree is None else debut + duree


class ProbeError(RuntimeError):
    """Le MEDIA est en cause : illisible, sans duree, sans piste video."""


class ProbeUnavailable(ProbeError):
    """La SONDE est en cause : binaire absent, bloque, ou sortie illisible.

    Distincte parce qu'elle ne se repare pas en refaisant le travail : un
    appelant qui reessaye (le repli CPU du rendu) doit renoncer et le dire,
    au lieu d'accuser ffmpeg de ce que ffprobe n'a pas su faire. Sous-classe
    de ProbeError pour que les appelants qui ne font pas la difference
    (download, remux_repair) gardent leur comportement.
    """


def _parse_probe_json(data: dict) -> MediaInfo:
    video = next((s for s in data["streams"] if s["codec_type"] == "video"), None)
    if video is None:
        raise ProbeError("Aucune piste video")
    duration = data["format"].get("duration")
    if duration is None:
        # Ne JAMAIS accuser un logiciel de capture : la cause mesuree sur une VOD
        # Twitch telechargee par ClipForge est un conteneur brut (MPEG-TS) ou un
        # fichier tronque, et aucun OBS n'y intervient. Le remede est de relancer
        # l'analyse, qui reindexe la source avant de la relire.
        raise ProbeError(
            "Durée indisponible : le conteneur n'est pas lisible ou le fichier est "
            "incomplet. Relance l'analyse — ClipForge réindexera la vidéo avant de "
            "la relire.")
    num, _, den = video.get("avg_frame_rate", "30/1").partition("/")
    if float(den or 1):
        fps = float(num) / float(den or 1)
    else:
        logger.warning("avg_frame_rate %r inexploitable : cadence supposee a 30 ips "
                       "(le montage V6 reechantillonne tout le clip sur cette cadence)",
                       video["avg_frame_rate"])
        fps = 30.0
    audio = next((s for s in data["streams"] if s["codec_type"] == "audio"), None)
    origine = _champ(data["format"], "start_time") or 0.0
    return MediaInfo(
        duration_s=float(duration),
        width=int(video["width"]), height=int(video["height"]), fps=fps,
        video_duration_s=_champ(video, "duration"), audio_duration_s=_champ(audio, "duration"),
        video_start_s=_debut(video, origine), audio_start_s=_debut(audio, origine),
        audio_present=audio is not None)


def _champ(flux: dict | None, cle: str) -> float | None:
    valeur = None if flux is None else flux.get(cle)
    return None if valeur in (None, "N/A") else float(valeur)


def _debut(flux: dict | None, origine: float) -> float:
    debut = _champ(flux, "start_time")
    return 0.0 if debut is None else debut - origine


def probe_media(path: Path | str) -> MediaInfo:
    if not Path(path).is_file():
        raise ProbeError(f"Fichier introuvable : {path}")
    try:
        proc = subprocess.run(
            ["ffprobe", "-v", "error", "-print_format", "json",
             "-show_format", "-show_streams", str(path)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            check=False, timeout=PROBE_TIMEOUT_S)
    except FileNotFoundError as exc:
        raise ProbeUnavailable(
            "ffprobe introuvable — installer ffmpeg (winget install Gyan.FFmpeg)") from exc
    except subprocess.TimeoutExpired as exc:
        raise ProbeUnavailable(
            f"ffprobe n'a pas rendu la main en {PROBE_TIMEOUT_S} s sur {Path(path).name}"
        ) from exc
    if proc.returncode != 0:
        raise ProbeError(f"ffprobe a echoue : {proc.stderr.strip()}")
    # Une sonde qui rend rc=0 et une sortie vide ou tronquee levait jusqu'ici
    # une JSONDecodeError (ou un KeyError) nue, que les appelants ne pouvaient
    # pas rattraper : ils annoncent tous ProbeError. Le contrat vaut mieux tenu.
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise ProbeUnavailable(
            f"sortie de ffprobe illisible : {proc.stdout[:200]!r}") from exc
    try:
        return _parse_probe_json(data)
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        # {} rendu par une sonde en panne levait KeyError('streams') brut, que
        # rien ne rattrapait : le rendu mourait avant meme d'essayer.
        raise ProbeUnavailable(
            f"sortie de ffprobe inattendue ({exc!r}) : {proc.stdout[:200]!r}") from exc
