"""Reecrire un conteneur defectueux, et refuser le remplacant avant la source.

L'autre moitie du remux : remux_detection.py nomme le defaut, ce module le
repare — la passe ffmpeg bornee en temps, le controle du fichier produit, et le
remplacement qui n'a lieu qu'une fois ce controle passe. La source d'origine
n'est jamais touchee avant que son remplacant ait fait ses preuves.
"""
import logging
import shutil
import subprocess
import threading
from collections.abc import Callable
from pathlib import Path

from clipforge.pipeline.probe import ProbeError, probe_media
from clipforge.pipeline.remux_detection import besoin_de_remux, is_fragmented

logger = logging.getLogger(__name__)

MIN_REMUX_RATIO = 0.5    # garde-fou : un remux qui perd la moitie du fichier est casse
DISK_MARGIN = 1.05       # le remux vit a cote de la source le temps de la passe
MIN_DUREE_RATIO = 0.5    # plancher de coherence, PAS une egalite (cf. _valider)
REMUX_TIMEOUT_S = 1800.0  # 30 min : large pour 20 Go en copie de flux, borne quand meme


class RemuxError(RuntimeError):
    pass


class SourceInexploitableError(RemuxError):
    """La SOURCE est en cause, pas l'environnement — et elle ne guerira pas seule.

    Deux familles d'echec cohabitaient sous un seul type. « Le disque est plein »,
    « le fichier est verrouille », « ffmpeg est absent » disent que l'operation
    n'a pas pu avoir lieu : la source est saine, il faut refaire la passe. La
    troncature, la non-convergence et la sortie illisible, elles, disent que ce
    fichier-la ne rendra jamais une video, quel que soit le nombre de reprises.

    Seule la seconde famille justifie d'ecarter la source pour la retelecharger.
    Le distinguer par un TYPE et non par une phrase : un disque plein ferait
    sinon renommer un fichier sain, puis payer vingt minutes de telechargement
    pour rien, sur un disque qui manquait deja de place.
    """


def _run_ffmpeg(cmd: list[str], total_s: float, progress_cb,
                timeout_s: float = REMUX_TIMEOUT_S) -> None:
    """Lance ffmpeg en relayant sa progression, et leve si la sortie n'est pas exploitable.

    timeout_s borne la passe. Mesure du 2026-08-30 : la reparation lancee par
    yt-dlp sur une VOD a coupures publicitaires relisait le fichier sans jamais
    converger — 90 Go relus pour 19,5 Go de source, 8,8 Mo ecrits en 90 s. Sans
    borne, l'etape reste figee et l'interface annonce « ~30 s restantes »
    indefiniment ; avec elle, l'echec est dit.
    """
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, encoding="utf-8", errors="replace")
    except OSError as exc:
        raise RemuxError(
            "ffmpeg introuvable — installer ffmpeg (winget install Gyan.FFmpeg)") from exc
    expire = {"oui": False}

    def _abandonner() -> None:
        expire["oui"] = True
        proc.kill()

    minuteur = threading.Timer(timeout_s, _abandonner)
    minuteur.daemon = True
    minuteur.start()
    # Le context manager ferme les tubes et attend le process quoi qu'il arrive :
    # une interruption pendant la lecture ne laisse pas un ffmpeg orphelin sur une
    # passe de plusieurs minutes.
    try:
        with proc:
            assert proc.stdout is not None
            for line in proc.stdout:
                # -progress ecrit des paires cle=valeur ; out_time_us donne l'avancee.
                if progress_cb and total_s > 0 and line.startswith("out_time_us="):
                    try:
                        done_s = int(line.split("=", 1)[1]) / 1_000_000
                    except ValueError:
                        continue
                    progress_cb(min(100.0, done_s / total_s * 100.0))
            stderr = proc.stderr.read() if proc.stderr else ""
    finally:
        minuteur.cancel()
    # L'ordre compte : un minuteur qui se declenche a l'instant meme ou ffmpeg
    # sortait normalement ne doit pas transformer un succes en abandon.
    if proc.returncode != 0 and expire["oui"]:
        raise SourceInexploitableError(
            f"Remux interrompu apres {timeout_s / 60:.0f} min sans converger — "
            "le fichier source est probablement endommage")
    if proc.returncode != 0:
        raise RemuxError(f"Remux echoue : {stderr[-300:]}")


def _args_de_remux(defaut: str) -> tuple[list[str], list[str]]:
    """Options ffmpeg (avant l'entree, apres l'entree) pour reparer ce defaut."""
    if defaut == "mpegts":
        # Mappage EXPLICITE d'une seule video et d'un seul audio : les coupures
        # publicitaires Twitch injectent en cours de VOD des flux de donnees
        # (timed_id3) aux paquets corrompus et sans horodatage. Le `-map 0` de
        # yt-dlp les embarquait et le multiplexeur MP4 attendait ensuite des DTS
        # qui n'arrivaient jamais. genpts reconstruit ceux qui manquent ; -f mp4
        # est explicite parce que le fichier peut porter n'importe quelle
        # extension, y compris .ts.
        return (["-fflags", "+genpts"],
                ["-map", "0:v:0", "-map", "0:a:0", "-c", "copy",
                 "-movflags", "+faststart", "-f", "mp4"])
    return ([], ["-c", "copy", "-movflags", "+faststart"])


def _valider(tmp: Path, defaut: str, taille_source: int, duration_s: float, nom: str) -> None:
    """Refuse un remplacant douteux AVANT de toucher a la source.

    Les deux defauts ne se controlent pas au meme instrument. Un fragmente
    reecrit garde tous ses flux : sa taille doit suivre celle de la source. Un
    MPEG-TS, lui, PERD volontairement ses flux surnumeraires et l'entete de ses
    paquets — le rapport de taille ne veut plus rien dire, ce qui compte est
    qu'il rende enfin une duree.

    Le plancher de duree est genereux et n'est PAS une egalite : quelques
    secondes d'ecart avec l'annonce de yt-dlp sont normales, et refuser a tort
    renverrait la VOD dans la boucle d'erreur que ce correctif supprime. Il ne
    se declenche que sur une troncature franche (un remux tue en cours de route).

    Sortie absente, illisible ou tronquee accusent la SOURCE : la passe est allee
    au bout, et c'est son resultat qui prouve que l'entree etait malade. D'ou le
    type SourceInexploitableError, sur lequel l'etape decide d'ecarter le
    fichier. « Toujours fragmente » reste un RemuxError nu : celui-la accuse la
    reparation, pas l'entree, et une source neuve n'y changerait rien.
    """
    if not tmp.is_file():
        raise SourceInexploitableError(f"Remux de {nom} sans sortie, source conservee")
    if defaut == "mpegts":
        try:
            duree = probe_media(tmp).duration_s
        except ProbeError as exc:
            raise SourceInexploitableError(
                f"Remux de {nom} toujours illisible, source conservee : {exc}") from exc
        if duree <= 0 or (duration_s > 0 and duree < duration_s * MIN_DUREE_RATIO):
            raise SourceInexploitableError(
                f"Remux de {nom} tronque ({duree:.0f} s pour {duration_s:.0f} s "
                f"attendues), source conservee")
        return
    if tmp.stat().st_size < taille_source * MIN_REMUX_RATIO:
        raise SourceInexploitableError(f"Remux de {nom} tronque, source conservee")
    if is_fragmented(tmp):
        raise RemuxError(f"Remux de {nom} toujours fragmente, source conservee")


def ensure_seekable(path: Path, *, duration_s: float = 0.0,
                    progress_cb: Callable[[float], None] | None = None,
                    dest: Path | None = None) -> bool:
    """Rend le fichier lisible et seekable si besoin. True s'il a ete reecrit.

    Deux conteneurs sont repares : le MP4 fragmente sans index, et le MPEG-TS
    brut que le HLS Twitch livre sous un nom .mp4. Le fichier d'origine n'est
    remplace qu'apres verification de son remplacant : une coupure de courant ou
    un ffmpeg tue laisse un .tmp a nettoyer, jamais une source de plusieurs Go
    corrompue.

    `dest` fourni : la version reparee est ECRITE LA, et `path` n'est pas touche
    — c'est le regime d'un fichier importe par l'utilisateur, qui lui appartient.
    L'appelant doit alors pointer la suite du pipeline sur `dest`.
    """
    defaut = besoin_de_remux(path)
    if defaut is None:
        return False
    cible = dest or path
    taille = path.stat().st_size
    cible.parent.mkdir(parents=True, exist_ok=True)
    libre = shutil.disk_usage(cible.parent).free
    if libre < taille * DISK_MARGIN:
        raise RemuxError(
            f"Espace disque insuffisant pour remuxer {path.name} : "
            f"{libre / 2**30:.1f} Go libres, {taille * DISK_MARGIN / 2**30:.1f} Go requis")
    tmp = cible.with_name(f"{cible.stem}.remux.tmp{cible.suffix}")
    tmp.unlink(missing_ok=True)
    avant_entree, apres_entree = _args_de_remux(defaut)
    try:
        _run_ffmpeg(
            ["ffmpeg", "-y", "-v", "error", "-nostats", *avant_entree, "-i", str(path),
             *apres_entree, "-progress", "pipe:1", str(tmp)],
            duration_s, progress_cb)
        _valider(tmp, defaut, taille, duration_s, path.name)
        try:
            tmp.replace(cible)
        except PermissionError as exc:
            # Windows verrouille un fichier ouvert : le moteur ClipForge sert
            # peut-etre la video au meme instant. La source reste intacte.
            raise RemuxError(
                f"{cible.name} est verrouille par un autre programme — fermer ClipForge "
                f"(ou la lecture en cours) puis relancer la migration") from exc
    except BaseException:
        tmp.unlink(missing_ok=True)   # le remplacement a echoue : pas de .tmp de plusieurs Go
        raise
    if dest is None:
        logger.info("%s remuxe en MP4 indexe (%.1f Go)", path.name, taille / 2**30)
    else:
        logger.info("%s copie en MP4 indexe sous %s (%.1f Go de plus sur le disque) : "
                    "un MPEG-TS importe rend un clip desynchronise, et le fichier "
                    "d'origine n'est pas reecrit",
                    path.name, cible.name, cible.stat().st_size / 2**30)
    return True
