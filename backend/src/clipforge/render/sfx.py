"""Banque d'effets sonores et emojis vendorises, chaine de normalisation loudness."""
import json
import logging
import math
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from clipforge.render.montage.motion import PunchWindow

logger = logging.getLogger(__name__)

ASSETS_DIR = Path(__file__).resolve().parent / "assets"
SFX_DIR = ASSETS_DIR / "sfx"
EMOJI_DIR = ASSETS_DIR / "emoji"

VENDORED_EMOJI = "💀🔥😱😂🤯💥❤️👀🎯🏆😭🤣💪🎉⚡🫡😈🥶🗿🤡"

IMPACT_AT_S = 0.05
WHOOSH_LEAD_S = 0.15
IMPACT_GAIN_DB = -12.0
WHOOSH_GAIN_DB = -14.0

_LOUDNORM_TARGET = "loudnorm=I=-14:TP=-1:LRA=11"
CLIP_PASS_TIMEOUT_S = 120  # une passe ffmpeg sur la fenetre du clip
_SFX_STEREO = "aformat=channel_layouts=stereo"
_OUTPUT_SAMPLE_RATE = 48000
_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)
_REQUIRED_MEASURED_KEYS = frozenset(
    {"input_i", "input_tp", "input_lra", "input_thresh", "target_offset"})


@dataclass(frozen=True)
class SfxEvent:
    at_s: float
    path: Path
    gain_db: float


def emoji_clusters(text: str) -> list[str]:
    """Segmente `text` en emojis individuels (codepoint de base + FE0F optionnel).

    Un variateur U+FE0F ne forme jamais un emoji a lui seul : il se rattache
    toujours au cluster precedent.
    """
    clusters: list[str] = []
    for ch in text:
        if ord(ch) == 0xFE0F and clusters:
            clusters[-1] += ch
        else:
            clusters.append(ch)
    return clusters


def codepoint_candidates(char: str) -> list[str]:
    """Noms de fichier Twemoji candidats pour `char` : sequence complete, puis base.

    Convention Twemoji : le variateur FE0F n'est pas toujours publie comme fichier
    a part (ex. coeur : `2764-fe0f.png` absent du depot, `2764.png` present). On
    tente donc la sequence complete d'abord et on replie sur la base sans FE0F.
    Utilise a la fois par fetch_emoji.py (telechargement) et emoji_path (resolution
    disque) pour que les deux ne puissent jamais diverger.
    """
    full = "-".join(f"{ord(c):x}" for c in char)
    base = "-".join(f"{ord(c):x}" for c in char if ord(c) != 0xFE0F)
    return [full] if full == base else [full, base]


_VENDORED_SET = frozenset(emoji_clusters(VENDORED_EMOJI))


def emoji_path(char: str) -> Path | None:
    """Chemin du PNG 72x72 vendorise pour `char`, ou None si hors du jeu embarque."""
    if char not in _VENDORED_SET:
        return None
    for name in codepoint_candidates(char):
        candidate = EMOJI_DIR / f"{name}.png"
        if candidate.is_file():
            return candidate
    return None


def sfx_plan(punches: list[PunchWindow], *, with_hook: bool,
            teaser_cut_s: float | None = None, max_sfx: int = 4) -> list[SfxEvent]:
    """Plan d'effets pour un clip : impact sur le hook, un whoosh par punch-in.

    `teaser_cut_s` (temps de coupe teaser->corps, EDL) ajoute un whoosh juste
    avant la coupe (meme avance WHOOSH_LEAD_S que les punches). Priorite juste
    apres l'impact du hook : il survit donc a la troncature `max_sfx` avant les
    whoosh de punch-in, qui sont moins critiques a l'oreille que la coupe elle-meme.
    Toujours tronque a `max_sfx` ; l'impact vient en tete quand `with_hook`.
    """
    events: list[SfxEvent] = []
    if with_hook:
        events.append(SfxEvent(IMPACT_AT_S, SFX_DIR / "impact.wav", IMPACT_GAIN_DB))
    if teaser_cut_s is not None:
        events.append(SfxEvent(max(0.0, teaser_cut_s - WHOOSH_LEAD_S), SFX_DIR / "whoosh.wav",
                               WHOOSH_GAIN_DB))
    for p in punches:
        events.append(SfxEvent(max(0.0, p.start - WHOOSH_LEAD_S), SFX_DIR / "whoosh.wav",
                               WHOOSH_GAIN_DB))
    return events[:max_sfx]


def _loudnorm_expr(measured: dict | None, *, json_output: bool = False) -> str:
    expr = _LOUDNORM_TARGET
    if measured is not None:
        expr += (
            f":measured_I={measured['input_i']}:measured_TP={measured['input_tp']}"
            f":measured_LRA={measured['input_lra']}:measured_thresh={measured['input_thresh']}"
            f":offset={measured['target_offset']}:linear=true"
        )
    if json_output:
        expr += ":print_format=json"
    return expr


def _mix_graph(events: list[SfxEvent], loud_expr: str, *, src: str = "[0:a]") -> str:
    # aresample apres loudnorm : loudnorm annonce un sample rate de sortie qui ne
    # correspond pas forcement a l'entree (ex. 192 kHz), et l'encodeur AAC s'y
    # ajuste sans le redire nulle part -> 96 kHz livre au lieu de 48 (Task 10, D2).
    tail = f"{loud_expr},aresample={_OUTPUT_SAMPLE_RATE}"
    statements: list[str] = []
    # aresample AVANT loudnorm/amix, uniquement sur le chemin de coupe (src != la
    # source brute par defaut "[0:a]") : [acut] vient d'un concat de plusieurs
    # atrim/asetpts, qui remet les PTS a zero a chaque segment. Combine a -ss/-t
    # en options d'entree (toujours le cas, cf. build_render_cmd) et a loudnorm en
    # aval, ffmpeg lit alors un DTS non monotone et degrade SILENCIEUSEMENT l'audio
    # (tronque ou reordonne, rc=0 -- bisection sur 18 rendus reels, finding 2 review
    # Task 7). Un aresample explicite juste apres la coupe nettoie ce reset avant
    # que loudnorm/amix ne le voient. V5 (src="[0:a]", jamais de concat en amont)
    # n'a pas ce probleme : condition sur src, pas un flag explicite, pour laisser
    # le golden V5 intact.
    if src != "[0:a]":
        statements.append(f"{src}aresample={_OUTPUT_SAMPLE_RATE}[acutrs]")
        src = "[acutrs]"
    if not events:
        statements.append(f"{src}{tail}[aout]")
        return ";".join(statements)
    delays = []
    labels = []
    for i, e in enumerate(events, start=1):
        delay_ms = round(e.at_s * 1000)
        labels.append(f"[sfx{i}]")
        # aformat=stereo sur CHAQUE branche SFX : les WAV embarques sont mono, et
        # sans cette contrainte amix negocie le plus petit denominateur commun de
        # channel layout entre les entrees et rabat la voix stereo sur un seul
        # canal — regression mesuree sur les clips V5 (Task 10, D1).
        delays.append(f"[{i}:a]adelay={delay_ms}|{delay_ms},{_SFX_STEREO},"
                      f"volume={e.gain_db}dB[sfx{i}]")
    # duration=first : sans cette clause, amix dure=longest par defaut et la queue
    # d'un effet pres de la fin du clip rallonge [aout] au-dela de [0:a] (la voix) —
    # decalage audio/video muet, injecte tel quel dans le rendu (Task 5).
    mix = (f"{src}{''.join(labels)}amix=inputs={len(events) + 1}:normalize=0:"
          f"duration=first[mixed]")
    statements += [*delays, mix, f"[mixed]{tail}[aout]"]
    return ";".join(statements)


def audio_filter(events: list[SfxEvent], *, measured: dict | None,
                 src: str = "[0:a]") -> tuple[list[str], str]:
    """Graphe ffmpeg complet : chaque effet adelay + attenue, amix, puis loudnorm.

    Retourne `(inputs, graphe)` : les `-i` supplementaires a ajouter a la commande
    ffmpeg (un par effet, dans l'ordre), et un filter_complex qui consomme `src`
    (par defaut `[0:a]` ; `[acut]` quand render.filtergraph.audio_cut est branche
    en amont) plus un flux par effet, et produit `[aout]`. `measured` (sortie de
    measure_loudness) bascule loudnorm en normalisation lineaire une passe ; None
    applique loudnorm en mode dynamique degrade.
    """
    inputs: list[str] = []
    for e in events:
        inputs += ["-i", str(e.path)]
    return inputs, _mix_graph(events, _loudnorm_expr(measured), src=src)


def measure_loudness(source: Path, *, start_s: float, end_s: float,
                     events: list[SfxEvent], audio_cut: str = "",
                     teaser_input: list[str] | None = None) -> dict | None:
    """Premiere passe loudnorm (I/TP/LRA/seuil mesures) sur l'extrait + effets.

    Nourrit la normalisation lineaire de la seconde passe (`audio_filter` avec
    `measured` renseigne). Toute panne — ffmpeg absent, timeout, sortie illisible
    ou incomplete — degrade en loudnorm une passe : jamais bloquant pour le rendu.

    `audio_cut` (produit par render.filtergraph.audio_cut) : quand non vide,
    prefixe au filter_complex de mesure et le mix lit `[acut]` au lieu de `[0:a]`
    — la mesure doit porter sur le MEME audio que le rendu final, coupes incluses.
    `teaser_input` (render.filtergraph.teaser_input_args) suit les effets, comme au rendu.
    """
    inputs: list[str] = []
    for e in events:
        inputs += ["-i", str(e.path)]
    src = "[acut]" if audio_cut else "[0:a]"
    graph = _mix_graph(events, _loudnorm_expr(None, json_output=True), src=src)
    if audio_cut:
        graph = audio_cut + ";" + graph
    cmd = ["ffmpeg", "-ss", str(start_s), "-t", str(round(end_s - start_s, 3)),
           "-i", str(source), *inputs, *(teaser_input or []), "-filter_complex", graph,
           "-map", "[aout]", "-f", "null", "-"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=CLIP_PASS_TIMEOUT_S, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("Mesure loudness impossible (%s) : normalisation degradee une passe", exc)
        return None
    if proc.returncode != 0:
        logger.warning("Mesure loudness echouee (ffmpeg code %s), normalisation degradee "
                       "une passe : %s", proc.returncode, proc.stderr[-400:])
        return None
    match = _JSON_BLOCK.search(proc.stderr)
    if match is None:
        logger.warning("Sortie loudnorm sans bloc JSON : normalisation degradee une passe")
        return None
    try:
        mesure = json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        logger.warning("JSON loudnorm illisible (%s) : normalisation degradee une passe", exc)
        return None
    if not _REQUIRED_MEASURED_KEYS.issubset(mesure):
        logger.warning("JSON loudnorm incomplet (cles manquantes : %s), normalisation "
                       "degradee une passe", sorted(_REQUIRED_MEASURED_KEYS - mesure.keys()))
        return None
    valeurs = {k: mesure[k] for k in sorted(_REQUIRED_MEASURED_KEYS)}
    try:
        finie = all(math.isfinite(float(v)) for v in valeurs.values())
    except (TypeError, ValueError):
        finie = False
    if not finie:
        logger.warning("Mesure loudnorm non finie (%s) : normalisation degradee une passe", valeurs)
        return None
    return mesure
