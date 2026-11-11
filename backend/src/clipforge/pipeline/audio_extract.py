import logging
import subprocess
import wave
from pathlib import Path

from clipforge.pipeline.probe import ProbeError, probe_media

logger = logging.getLogger(__name__)

SR_SORTIE = 16000

# Un pas de fine_energy. Mesure (ffmpeg 9.0.1) : une extraction saine rend la
# duree demandee a l'echantillon pres, jusqu'a 13,4 ms de moins quand elle part
# du debut d'un flux AAC (delai d'encodeur).
TOLERANCE_DUREE_S = 0.05

# Sous la milliseconde, un start_time tient de l'arrondi de timebase du
# conteneur et non d'un retard a rattraper. Mesure ffmpeg 9.0.1 sur sources
# fabriquees, MP4 et MPEG-TS : une source saine rend 0,0 tout rond, donc ce
# plancher ne decide de rien pour elle ; les sources retardees mesurees vont
# de 8,7 ms a 2,96 s et sont toutes tres au-dessus.
RETARD_MINIMAL_S = 0.001


class AudioExtractError(RuntimeError):
    pass


def _retard_de_tete_s(source: Path, start_s: float) -> float:
    """De combien la piste audio commence APRES le debut de la fenetre voulue.

    ffmpeg ne comble pas un trou de tete : il part du premier echantillon
    disponible et sort rc=0. Sur une source dont l'audio commence a 2,96 s,
    l'extraction de la VOD entiere rend un WAV de 27,05 s ou un son de 4,0 s
    tombe a 1,04 s — et les horodatages Whisper, donc les sous-titres et les
    coupes, heritent du decalage sans que rien ne le signale. L'ecart mesure
    vaut exactement ce retard, sur MP4 comme sur MPEG-TS.

    Une source non sondable n'empeche pas d'extraire : on repart du
    comportement d'avant, et le journal le dit.
    """
    try:
        info = probe_media(source)
    except ProbeError as exc:
        logger.warning("Source %s non sondable (%s) : extraction sans rattrapage de "
                       "tete, un eventuel retard de la piste audio resterait",
                       source.name, exc)
        return 0.0
    return max(0.0, info.audio_start_s - start_s)


def extract_wav(source: Path | str, dest: Path | str, *,
                start_s: float | None = None, end_s: float | None = None,
                timeout_s: float | None = None) -> Path:
    """Extrait l'audio en WAV mono 16 kHz, cale sur le debut de la fenetre,
    ecrit via un .part renomme a la fin.

    VOD entiere : une piste audio en retard se voit precedee du silence qui
    manque, pour que l'instant t du WAV reste l'instant t de la source.

    Fenetre bornee : le meme rattrapage est impossible, et le cas est refuse
    plutot que rendu faux. Mesure ffmpeg 9.0.1 sur `-ss 0 -t 6` d'une source
    dont l'audio commence a 2,96 s : le silence ajoute par filtre et `-t` se
    disputent la duree, et les quatre montages essayes (`-t` avant ou apres
    `-af`, `-to`, `atrim`+`apad`) rendent 3,04 s de silence au lieu des 6,0 s
    attendues avec le son a 4,0 s. Une fenetre qui commence APRES le debut de
    l'audio, elle, n'a rien a rattraper et sort juste a l'echantillon.

    Une piste qui part a l'heure n'est pas touchee, octet pour octet.

    rc=0 ne prouve rien pour autant : un seek au-dela de la fin, une fenetre a
    cheval sur la fin ou une piste audio plus courte que la video sortent en
    succes avec un WAV vide ou tronque (revue finale V6, sondes ffmpeg 9.0.1).
    Le WAV est donc relu : 0 trame, ou en mode borne une duree sous ce que la
    source a vraiment d'audio a cet endroit moins TOLERANCE_DUREE_S, leve
    AudioExtractError. `timeout_s` borne ffmpeg (None : aucune borne).
    """
    if not Path(source).is_file():
        raise AudioExtractError(f"Fichier introuvable : {source}")
    if (start_s is None) != (end_s is None):
        raise ValueError("start_s et end_s doivent etre fournis ensemble ou pas du tout")
    if start_s is not None and end_s is not None and end_s <= start_s:
        raise ValueError("end_s doit etre strictement superieur a start_s")
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    cmd = ["ffmpeg", "-y"]
    if start_s is not None:
        cmd.extend(["-ss", str(start_s)])
    cmd.extend(["-i", str(source)])
    duration = None
    if start_s is not None and end_s is not None:
        duration = round(end_s - start_s, 3)
        cmd.extend(["-t", str(duration)])
    retard = _retard_de_tete_s(Path(source), start_s or 0.0)
    if retard >= RETARD_MINIMAL_S:
        if start_s is not None:
            raise AudioExtractError(
                f"Piste audio qui commence {retard:.3f} s apres le debut de la fenetre "
                f"({start_s} s) : le WAV extrait serait decale d'autant")
        # Le delai se compte en echantillons DE SORTIE : le resample precede
        # donc adelay, sinon le « S » compterait dans la frequence de la source.
        echantillons = round(retard * SR_SORTIE)
        logger.info("Piste audio de %s en retard de %.3f s : %d echantillons de "
                    "silence ajoutes en tete pour recaler le WAV sur la source",
                    Path(source).name, retard, echantillons)
        cmd.extend(["-af", f"aresample={SR_SORTIE},adelay={echantillons}S:all=1"])
    cmd.extend(["-vn", "-ac", "1", "-ar", str(SR_SORTIE),
                "-c:a", "pcm_s16le", "-f", "wav", str(tmp)])
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            check=False, timeout=timeout_s)
    except FileNotFoundError as exc:
        raise AudioExtractError(
            "ffmpeg introuvable — installer ffmpeg (winget install Gyan.FFmpeg)") from exc
    except subprocess.TimeoutExpired as exc:
        tmp.unlink(missing_ok=True)
        raise AudioExtractError(
            f"Extraction audio interrompue : ffmpeg n'a pas rendu la main en {timeout_s} s"
        ) from exc
    if proc.returncode != 0 or not tmp.is_file():
        tmp.unlink(missing_ok=True)
        raise AudioExtractError(f"Extraction audio echouee : {proc.stderr[-400:]}")
    try:
        _verifier_wav(tmp, duration, Path(source), start_s)
    except AudioExtractError:
        tmp.unlink(missing_ok=True)
        raise
    tmp.replace(dest)
    return dest


def _verifier_wav(wav: Path, duree_demandee: float | None,
                  source: Path, start_s: float | None) -> None:
    try:
        with wave.open(str(wav)) as w:
            trames, sr = w.getnframes(), w.getframerate()
    except (wave.Error, EOFError) as exc:
        raise AudioExtractError(f"WAV extrait illisible : {exc}") from exc
    if trames == 0:
        raise AudioExtractError("Extraction audio vide : 0 echantillon (source sans audio "
                                "a cet endroit, ou seek au-dela de la fin)")
    lue = trames / sr
    if duree_demandee is None or lue >= duree_demandee - TOLERANCE_DUREE_S:
        return
    dispo = _audio_disponible(source, start_s or 0.0, duree_demandee)
    if lue >= dispo - TOLERANCE_DUREE_S:
        return
    raise AudioExtractError(
        f"Extraction audio tronquee : {lue:.3f} s lues pour {duree_demandee} s "
        f"demandees et {dispo:.3f} s disponibles (piste audio plus courte, ou "
        "fenetre au-dela de la fin)")


def _audio_disponible(source: Path, start_s: float, duree_demandee: float) -> float:
    """Ce que la source a vraiment d'audio a partir de start_s, borne au demande.

    Un flux audio qui finit avant son conteneur rend une extraction saine plus
    courte que la fenetre demandee (mesure sur source fabriquee : audio de
    9,92 s sous un conteneur de 10,0 s, 5,920 s lues pour 6,0 s demandees,
    80 ms d'avance). La fin du flux est son debut plus sa duree. Un flux qui
    COMMENCE apres start_s, lui, est refuse avant meme de lancer ffmpeg
    (_retard_de_tete_s) : il ne se rattrape pas sur une fenetre bornee.
    Source non sondable : la fenetre demandee reste la reference, comme
    avant, et le journal le dit.
    """
    try:
        info = probe_media(source)
    except ProbeError as exc:
        logger.warning("Source %s non sondable (%s) : extraction jugee sur la fenetre "
                       "demandee", source.name, exc)
        return duree_demandee
    if info.audio_end_s is None:
        return duree_demandee
    return min(duree_demandee, max(0.0, info.audio_end_s - start_s))
