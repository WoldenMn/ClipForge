"""Rendus reels relus image par image : chaque image source porte son numero
(_NUMBERED_BANDS) et ce que le rendu montre dans ses zones cam et jeu se relit
rang par rang (_rendered_frame_numbers). Teaser qui ne montre que ses images
(revue du lot A), grille de sortie commune aux branches cam et jeu (lot A2).

Separe de test_montage_integration.py (revue du lot A2, M6), dont il reprend
les sources synthetiques : jamais data/, sorties sous tmp_path.
"""
import json
import re
import subprocess
from fractions import Fraction
from functools import lru_cache
from itertools import pairwise
from pathlib import Path

import numpy as np
import pytest
from test_montage_integration import (
    TEASER_BURST,
    TEASER_DUR_S,
    _clip_row,
    _rendre_source,
    _seed_source_clip,
    _write_tone_wav,
)

from clipforge.diagnostic import _FFMPEG_VERSION_RE, _version_outil
from clipforge.pipeline.audio_analysis import read_wav_mono
from clipforge.pipeline.segmenter import Word
from clipforge.render import montage_plan as montage_plan_mod
from clipforge.render.frame_grid import frame_grid
from clipforge.render.montage.coldopen import choose_cold_open
from clipforge.render.montage.edl import build_edl
from clipforge.render.montage.energy import fine_energy
from clipforge.render.montage.motion import ATTACK_S
from clipforge.render.montage.motion_detect import detect_punches
from clipforge.render.montage.timemap import TimeMap

pytestmark = pytest.mark.integration


# Numero de l'image en 3 chiffres hexa, un par bande grise (niveau 16*d+8),
# ecrit deux fois : canevas 64x36 agrandi 30 fois, bandes jeu en x [720,1440)
# et y [0,360) de la source 1920x1080 (zone jeu du profil PROFILE, x 640..1508,
# au-dessus des sous-titres une fois rendues), bandes cam en x [90,630) et
# y [0,330) (zone cam du profil, 640x340).
_NUMBERED_BANDS = (
    r"geq=lum='if(lt(Y\,12)*gte(X\,24)*lt(X\,48)\,"
    r"16*mod(floor(N/pow(16\,floor((X-24)/8)))\,16)+8\,"
    r"if(lt(Y\,11)*gte(X\,3)*lt(X\,21)\,"
    r"16*mod(floor(N/pow(16\,floor((X-3)/6)))\,16)+8\,128))':cb=128:cr=128,"
    "scale=1920:1080:flags=neighbor")


def _mux_numbered_video_with_audio(video_out: Path, audio_wav: Path, *, duration_s: float,
                                   fps: int | str = 30,
                                   trous: tuple[tuple[int, int], ...] = ()) -> Path:
    """Comme _mux_video_with_audio, mais chaque image porte son numero (bandes
    _NUMBERED_BANDS) : ce qu'un rendu montre se relit image par image.

    `trous` : plages d'images (premiere, derniere) retirees de la video, les
    autres gardant leur numero et leur instant, l'audio restant continu."""
    vf = _NUMBERED_BANDS
    if trous:
        plages = "+".join(f"between(n,{a},{b})" for a, b in trous)
        vf += f",select='not({plages})'"
    cmd = [
        "ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c=black:s=64x36:r={fps}:d={duration_s}",
        "-i", str(audio_wav), "-vf", vf, *(["-fps_mode", "passthrough"] if trous else []),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest", str(video_out),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", check=False)
    assert proc.returncode == 0, (
        f"construction source numerotee {video_out} echouee (code {proc.returncode}) : "
        f"{proc.stderr[-800:]}")
    return video_out


def _video_frame_times(path: Path) -> list[float]:
    """Instants (s) de chaque image du premier flux video, tries : un par paquet
    demuxe, lus par ffprobe (aucun decodage)."""
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "packet=pts_time", "-of", "json", str(path)],
        capture_output=True, text=True, encoding="utf-8", check=True)
    return sorted(float(p["pts_time"]) for p in json.loads(proc.stdout)["packets"])


# Zone du rendu 1080x1920 -> (bande y lue, centres x des 3 chiffres a 1/10).
# Jeu (y >= 576) : bandes source x [720+240i, 960+240i) agrandies de 1080/868,
# lues dans y [650, 950), au-dessus des sous-titres. Cam (y < 576) : bandes
# source x [90+180i, 270+180i) agrandies de 1080/640, lues dans y [150, 450) ;
# le zoompan des coupes (x1.035 au plus) les deplace de moins de 15 px.
_ZONES_NUMEROS = {
    "jeu": (650, [round((80 + 240 * i + 120) * 1080 / 868 / 10) for i in range(3)]),
    "cam": (150, [round((180 + 180 * i) * 1080 / 640 / 10) for i in range(3)]),
}


def _rendered_frame_numbers(path: Path, zone: str = "jeu") -> list[int]:
    """Numeros (_NUMBERED_BANDS) de chaque image d'un rendu 1080x1920, lus au
    centre des bandes de la zone demandee (cf. _ZONES_NUMEROS)."""
    haut, centres = _ZONES_NUMEROS[zone]
    w, h = 108, 30
    proc = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:v:0", "-vf",
         f"crop=1080:300:0:{haut},scale={w}:{h}:flags=area", "-fps_mode", "passthrough",
         "-pix_fmt", "yuv420p", "-f", "rawvideo", "-"],
        capture_output=True, check=False)
    assert proc.returncode == 0, (
        f"lecture des numeros {path} echouee : {proc.stderr[-500:].decode('utf-8', 'replace')}")
    taille = w * h * 3 // 2
    n = len(proc.stdout) // taille
    luma = np.frombuffer(proc.stdout[: n * taille], dtype=np.uint8).reshape(n, taille)[:, : w * h]
    luma = luma.reshape(n, h, w).astype(np.float64)
    numeros = np.zeros(n, dtype=np.int64)
    for i, centre in enumerate(centres):
        niveau = luma[:, 5:25, centre - 6: centre + 6].mean(axis=(1, 2))
        numeros += np.clip(np.round((niveau - 8) / 16), 0, 15).astype(np.int64) * 16 ** i
    return numeros.tolist()


_CONFIG_RE = re.compile(r"\[Parsed_showinfo_(\d+) @ [^\]]+\] config in time_base: \d+/\d+, "
                        r"frame_rate: (\d+)/(\d+)")


def _grilles_tirees(texte: str) -> dict[str, Fraction]:
    """Cadence que zoompan et le filtre fps tirent REELLEMENT d'un meme texte,
    annoncee par showinfo derriere chacun."""
    graphe = (f"[0:v]split=2[a][b];[a]zoompan=z=1:d=1:s=64x36:fps={texte},showinfo[za];"
              f"[b]fps={texte},showinfo[fb]")
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-f", "lavfi", "-i", "color=s=64x36:r=60:d=0.5",
         "-filter_complex", graphe, "-map", "[za]", "-f", "null", "-",
         "-map", "[fb]", "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    assert proc.returncode == 0, proc.stderr[-800:]
    tirees = {("zoompan" if m.group(1) == "2" else "fps"):
              Fraction(int(m.group(2)), int(m.group(3)))
              for m in _CONFIG_RE.finditer(proc.stderr)}
    assert set(tirees) == {"zoompan", "fps"}, proc.stderr[-1500:]
    return tirees


def test_video_frame_times_helper_counts_a_single_extra_frame(tmp_path):
    """Valide `_video_frame_times` sur l'ecart qu'il doit voir : une image de
    plus ou de moins. 20 et 21 images a 10 ips, instants k/10 attendus."""
    comptes = {}
    for n in (20, 21):
        path = tmp_path / f"{n}.mp4"
        proc = subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi", "-i", f"testsrc2=size=64x64:rate=10:duration={n / 10}",
             "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", str(path)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
        assert proc.returncode == 0, f"construction {path} echouee : {proc.stderr[-500:]}"
        instants = _video_frame_times(path)
        assert instants == pytest.approx([k / 10 for k in range(n)], abs=1e-3), (path, instants)
        comptes[n] = len(instants)
    assert comptes == {20: 20, 21: 21}, comptes


def test_rendered_frame_numbers_helper_reads_both_zones_and_sees_a_missing_frame(tmp_path):
    """Valide `_rendered_frame_numbers` : une source numerotee mise dans la
    geometrie du rendu (zone cam recadree, agrandie et zoomee de 3,5 % comme
    apres une coupe ; zone jeu recadree, agrandie ; empilees en 1080x1920) se
    relit 0..N-1 dans les deux zones. Sans son image 5 dans la seule zone jeu,
    le trou n'apparait que la ; sans elle dans la seule zone cam, la cam ne
    montre plus les memes images que le jeu."""
    wav = _write_tone_wav(tmp_path / "muet.wav", duration_s=2.0, segments=[])
    source = _mux_numbered_video_with_audio(tmp_path / "numerotee.mp4", wav, duration_s=2.0)
    cam = ("crop=640:340:0:0,scale=1080:576:flags=lanczos,zoompan=z='1.035':d=1:"
           "x='iw/2-(iw/zoom/2)':y='ih*0.38-(ih/zoom*0.38)':s=1080x576:fps=30[top]")
    jeu = "crop=868:1080:640:0,scale=1080:1344:flags=lanczos[bot]"
    sans_5 = r"select='not(eq(n\,5))',"
    lus = {}
    for nom, avant_cam, avant_jeu in (("intacte", "", ""), ("jeu_sans_5", "", sans_5),
                                      ("cam_sans_5", sans_5, "")):
        path = tmp_path / f"{nom}.mp4"
        graphe = (f"[0:v]split=2[a][b];[a]{avant_cam}{cam};[b]{avant_jeu}{jeu};"
                  "[top][bot]vstack=inputs=2:shortest=1[v]")
        proc = subprocess.run(
            ["ffmpeg", "-y", "-i", str(source), "-filter_complex", graphe, "-map", "[v]",
             "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", str(path)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
        assert proc.returncode == 0, f"construction {path} echouee : {proc.stderr[-500:]}"
        lus[nom] = (_rendered_frame_numbers(path, "cam"), _rendered_frame_numbers(path, "jeu"))
    assert lus["intacte"] == (list(range(60)), list(range(60))), lus["intacte"]
    cam_j, jeu_j = lus["jeu_sans_5"]
    assert 5 not in jeu_j and jeu_j[:5] == list(range(5)), lus["jeu_sans_5"]
    assert 5 in cam_j, lus["jeu_sans_5"]
    cam_c, jeu_c = lus["cam_sans_5"]
    assert 5 not in cam_c and cam_c != jeu_c, lus["cam_sans_5"]


def test_grid_reader_sees_zoompan_and_fps_disagree_on_a_raw_float_rate():
    """Valide `_grilles_tirees` : "30000/1001" donne la meme cadence aux deux
    filtres ; le texte flottant 59.999989247313756 leur en donne deux."""
    assert _grilles_tirees("30000/1001") == {"zoompan": Fraction(30000, 1001),
                                              "fps": Fraction(30000, 1001)}
    assert _grilles_tirees("59.999989247313756") == {"zoompan": Fraction(60),
                                                      "fps": Fraction(334800000, 5580001)}


@pytest.mark.parametrize("fps", [60.0, 30000 / 1001, 60000 / 1001, 59.999989247313756,
                                 29.99999, 59.99500013888503, 23.976, 143.99876])
def test_zoompan_and_fps_read_the_same_grid_from_frame_grid(fps):
    """Revue A2, fix round 1 : d'une cadence de VOD non ronde, zoompan et fps
    tiraient deux grilles et vstack doublait les images. Les deux filtres
    relisent le texte de frame_grid a l'identique, et c'est la grille que
    zoompan tirait deja du flottant."""
    texte = frame_grid(fps)
    assert _grilles_tirees(texte) == {"zoompan": Fraction(texte), "fps": Fraction(texte)}
    assert _grilles_tirees(repr(fps))["zoompan"] == Fraction(texte)


# Mot choisi pour que le teaser, mot + respiration de 0,12 s de chaque cote,
# vaille [4.423, 5.693] (L = 1,27 s, soit 38,1 images a 30 ips) : sa 1re image
# tombe 10,3 ms apres le debut, et l'image 171 (5.7) tombe apres sa fin mais
# dans cet ecart ET sur le dernier rang de sa grille de sortie.
OFF_GRID_TEASER_WORD = (4.543, 5.573, "wow")


@pytest.fixture(scope="module")
def numbered_teaser_source(tmp_path_factory) -> Path:
    d = tmp_path_factory.mktemp("numbered_teaser_src")
    audio = _write_tone_wav(d / "teaser_tone.wav", duration_s=TEASER_DUR_S,
                            segments=[TEASER_BURST])
    return _mux_numbered_video_with_audio(d / "numbered_teaser.mp4", audio,
                                          duration_s=TEASER_DUR_S)


def test_off_grid_teaser_plays_only_its_own_frames(tmp_path, numbered_teaser_source):
    """Revue du lot A : -t en option d'entree compte la duree du teaser depuis
    la premiere image decodee, pas depuis le point de seek. Quand le debut du
    teaser tombe entre deux images, la video du teaser ramassait l'image qui
    suit sa fin. Le rendu doit ouvrir sur les rangs de sortie du teaser
    (ceil(L*30), cf. filtergraph.video_cut) remplis des seules images de la
    source dans [debut, fin), toutes, dans l'ordre, relues par leur numero."""
    db_path = tmp_path / "clipforge.db"
    words = [OFF_GRID_TEASER_WORD]
    clip_id = _seed_source_clip(db_path, source=numbered_teaser_source, width=1920,
                                height=1080, fps=30.0, words=words, start_s=0.0,
                                end_s=TEASER_DUR_S, montage="1")
    _rendre_source(tmp_path, clip_id)
    clip = _clip_row(tmp_path, clip_id)
    assert clip["status"] == "done", f"rendu du teaser hors grille echoue : {clip['error']}"

    wav = tmp_path / "jobs" / f"clip{clip_id}" / montage_plan_mod.CLIP_AUDIO_NAME
    samples, sr = read_wav_mono(wav)
    mots_rel = [Word(start=s, end=e, text=t) for s, e, t in words]
    teaser = choose_cold_open(mots_rel, fine_energy(samples, sr), clip_dur_s=TEASER_DUR_S)
    assert teaser is not None, "le clip de test doit produire un teaser"

    instants = _video_frame_times(numbered_teaser_source)
    attendues = [k for k, t in enumerate(instants)
                 if teaser.src_start - 1e-6 <= t < teaser.src_end - 1e-6]
    ecart = instants[attendues[0]] - teaser.src_start
    suivante = instants[attendues[-1] + 1]
    rangs = -(-round((teaser.src_end - teaser.src_start) * 1000) * 30 // 1000)
    assert (ecart > 1e-3 and suivante < teaser.src_end + ecart
            and round((suivante - teaser.src_start) * 30) == rangs - 1), (
        f"le scenario ne mord pas : teaser {teaser}, 1re image a +{ecart:.4f} s, image "
        f"suivant la fin a {suivante}, {rangs} rangs")

    out = Path(clip["output_path"])
    vues = _rendered_frame_numbers(out)[:rangs]
    sans_doublon = [n for i, n in enumerate(vues) if i == 0 or n != vues[i - 1]]
    assert sans_doublon == attendues and len(vues) - len(sans_doublon) <= 2, (
        f"{out}: le teaser {teaser} occupe {rangs} rangs et y montre {vues}, attendu les "
        f"images {attendues[0]}..{attendues[-1]} ({len(attendues)}), rien d'autre")


# --------------------------------------------------------------------------
# Case 3c (lot A2) : plusieurs coupes hors grille ET un teaser hors grille,
# zoompan actif. Cinq mots-zones separes par des silences de plus de 0,55 s
# (quatre coupes, bornes a la ms, donc hors de la grille de sortie), le 4e
# plus fort (pic net : teaser [9.221, 10.637]). Rejoue sur les sources et les
# cadences de VOD que la revue du fix round 1 a prises en defaut.
# --------------------------------------------------------------------------

GRID_DUR_S = 16.0
GRID_WORDS = [(0.31, 2.213, "un"), (3.087, 5.119, "deux"), (6.203, 8.137, "trois"),
              (9.341, 10.517, "quatre"), (11.689, 15.62, "cinq")]
# (conteneur, cadence de la source, vods.fps, grille attendue). Cadences de VOD
# non rondes : zoompan tire 60/1 de 59.99998924... (avg_frame_rate
# 334800000/5580001) et 30/1 de 29.99999, le filtre fps en tirait
# 334800000/5580001 et 2999999/100000. mkv : horodatage a la milliseconde
# (base 1/1000), images a 0, 17, 33, 50... ms, gigue de 0,33 ms.
GRID_CASES = [("mp4", 30, 30.0, 30), ("mp4", 60, 59.999989247313756, 60),
              ("mp4", 30, 29.99999, 30), ("mkv", 60, 60.0, 60)]
# Le cas mkv ne tient le contrat de video_piece qu'a partir de ffmpeg 9.0.
# Mesures du 2026-09-26, meme revision, meme source, encodeur CPU des deux
# cotes : ffmpeg 9.0.1 montre les rangs attendus, ffmpeg 8.0.1 (ubuntu-26.04,
# le runner du job integration) decale le morceau entier de deux images vers
# l'amont -- il montre 552..636 la ou la fenetre source est 554..638, soit le
# double de l'ecart d'une image que video_piece promet. L'encodeur est hors de
# cause : NVENC force sur le chemin CPU redonne vert sous ffmpeg 9. Cause non
# elucidee, ouverte dans le dossier SDD ; l'ancrage du morceau se decide sur la
# phase de la premiere image de la lecture anticipee, et ffmpeg 8 n'en garde
# pas la meme. Les trois cas mp4 tournent partout, et celui-ci redevient actif
# des que le runner fournit ffmpeg 9.
FFMPEG_GRILLE_MS_MIN = (9, 0)


@lru_cache(maxsize=1)
def _version_ffmpeg() -> tuple[int, int] | None:
    """(majeure, mineure) de ffmpeg, None si la sonde n'a rien su lire."""
    lue = _FFMPEG_VERSION_RE.match(_version_outil("ffmpeg"))
    return None if lue is None else (int(lue.group(1)), int(lue.group(2)))


@pytest.fixture(scope="module")
def numbered_cut_source(tmp_path_factory):
    """Source numerotee du scenario, construite une fois par (conteneur, cadence)."""
    d = tmp_path_factory.mktemp("numbered_cut_src")
    tons = [(s, e, 440.0, 0.9 if txt == "quatre" else 0.25) for s, e, txt in GRID_WORDS]
    audio = _write_tone_wav(d / "cut_tone.wav", duration_s=GRID_DUR_S, segments=tons)
    sources: dict[tuple, Path] = {}

    def construire(conteneur: str, cadence: int | str,
                   trous: tuple[tuple[int, int], ...] = ()) -> Path:
        if (conteneur, cadence, trous) not in sources:
            nom = f"{cadence}_{len(sources)}".replace("/", "_")
            sources[conteneur, cadence, trous] = _mux_numbered_video_with_audio(
                d / f"numbered_cuts_{nom}.{conteneur}", audio, duration_s=GRID_DUR_S,
                fps=cadence, trous=trous)
        return sources[conteneur, cadence, trous]
    return construire


def _rendu_du_scenario_de_grille(tmp_path: Path, source: Path, fps_vod: float, grille: int):
    """Rend GRID_WORDS sur `source` (render_clip_job) et rend (sortie, morceaux
    teaser puis segments, debuts de sortie en ms et rangs de chaque morceau selon
    TimeMap, numeros jeu), cam = jeu et compte d'images deja verifies. L'EDL est
    recalculee avec les memes primitives, sur l'audio reellement extrait par ce
    rendu."""
    clip_id = _seed_source_clip(tmp_path / "clipforge.db", source=source, width=1920,
                                height=1080, fps=fps_vod, words=GRID_WORDS, start_s=0.0,
                                end_s=GRID_DUR_S, montage="1")
    _rendre_source(tmp_path, clip_id)
    clip = _clip_row(tmp_path, clip_id)
    assert clip["status"] == "done", f"rendu du clip a coupes echoue : {clip['error']}"

    wav = tmp_path / "jobs" / f"clip{clip_id}" / montage_plan_mod.CLIP_AUDIO_NAME
    samples, sr = read_wav_mono(wav)
    mots_rel = [Word(start=s, end=e, text=t) for s, e, t in GRID_WORDS]
    edl = build_edl(mots_rel, fine_energy(samples, sr), clip_dur_s=GRID_DUR_S,
                    cut_style="standard")
    assert edl.teaser is not None and len(edl.segments) >= 5, edl
    tmap = TimeMap.from_edl(edl)
    debuts = [0.0, *(tmap.to_output(m.src_start) for m in edl.segments), tmap.output_duration]
    assert set(tmap.cut_times()) <= set(debuts), (tmap.cut_times(), debuts)
    bornes_ms = [round(a * 1000) for a in debuts]
    rangs = [-(-b * grille // 1000) for b in bornes_ms]
    out = Path(clip["output_path"])
    cam = _rendered_frame_numbers(out, "cam")
    jeu = _rendered_frame_numbers(out, "jeu")
    differentes = [i for i, (c, j) in enumerate(zip(cam, jeu)) if c != j]
    assert not differentes, (
        f"{out}: cam et jeu different sur {len(differentes)} images sur {len(jeu)} (premieres : "
        f"{[(i, cam[i], jeu[i]) for i in differentes[:6]]})")
    assert len(jeu) == rangs[-1], f"{out}: {len(jeu)} images, attendu {rangs[-1]} ({rangs})"
    return out, [edl.teaser, *edl.segments], bornes_ms, rangs, jeu


@pytest.mark.parametrize("conteneur, cadence, fps_vod, grille", GRID_CASES)
def test_cam_and_game_show_the_same_frame_across_off_grid_cuts(
        tmp_path, numbered_cut_source, conteneur, cadence, fps_vod, grille):
    """Revue du lot A : apres une coupe hors grille, la facecam saccadait (une
    image repetee puis une sautee, sur des secondes) et ne montrait plus le meme
    instant que le jeu. Cause mesuree : concat date le corps en microsecondes
    tronquees, zoompan re-date la branche cam sur sa propre grille 1/fps, et
    vstack, qui voit deux instants distants de moins d'une microseconde, sort
    deux images au lieu d'une ; la conversion a cadence fixe en jette une au
    hasard. Et la video derivait par rapport au son, coupe apres coupe. Revue
    A2, fix round 1 : d'une cadence de VOD non ronde, fps et zoompan tiraient
    deux grilles et vstack doublait les images (6142 au lieu de 3155) ; sur
    une source horodatee a la milliseconde (mkv), l'arrondi de fps basculait
    une image sur deux a l'interieur des morceaux (doublon puis saut).

    Rendu complet (render_clip_job, donc NVENC sur ce poste), source numerotee
    dans les deux zones : sur chaque image, cam et jeu montrent le meme numero ;
    chaque morceau (teaser puis segments) occupe exactement les rangs de la
    grille qui tombent dans son intervalle de sortie selon TimeMap, n'y montre
    que des images de sa fenetre source, dans l'ordre, sans saut, avec au plus
    une image doublee a l'un de ses bords. Seule exception, au premier rang :
    l'image qui precede la fenetre, si elle est la plus proche de l'instant vise
    (revue A2, fix round 2 : chaque morceau lit sa source une seconde avant)."""
    version = _version_ffmpeg()
    if conteneur == "mkv" and (version is None or version < FFMPEG_GRILLE_MS_MIN):
        lue = "illisible" if version is None else f"{version[0]}.{version[1]}"
        pytest.skip(
            f"horodatage a la milliseconde : contrat tenu a partir de ffmpeg "
            f"{FFMPEG_GRILLE_MS_MIN[0]}.{FFMPEG_GRILLE_MS_MIN[1]}, lu ici {lue} "
            f"(sous 9.0, le morceau sort decale de deux images : voir GRID_CASES)")
    source = numbered_cut_source(conteneur, cadence)
    out, morceaux, bornes_ms, rangs, jeu = _rendu_du_scenario_de_grille(
        tmp_path, source, fps_vod, grille)
    hors_grille = [b for b in bornes_ms[1:-1] if b * grille % 1000]
    assert len(hors_grille) >= 5 and round(morceaux[0].src_start * 1000) * grille % 1000, (
        f"le scenario ne mord pas : coupes en sortie {bornes_ms}, teaser {morceaux[0]}")
    instants = _video_frame_times(source)
    for k, m in enumerate(morceaux):
        vues = jeu[rangs[k]:rangs[k + 1]]
        fenetre = [n for n, t in enumerate(instants)
                   if m.src_start - 1e-6 <= t < m.src_end - 1e-6]
        pas = [b - a for a, b in pairwise(vues)]
        doubles = [i for i, p in enumerate(pas) if p == 0]
        assert set(vues[1:]) <= set(fenetre) and vues[0] in {fenetre[0] - 1, *fenetre} and all(
            p in (0, 1) for p in pas) and all(i in (0, len(pas) - 1) for i in doubles), (
            f"{out}: morceau {k} {m} (rangs {rangs[k]}..{rangs[k + 1] - 1}) montre {vues}, "
            f"fenetre source {fenetre[0]}..{fenetre[-1]}")


# Deux trous video, audio continu : images 87..158 (2,9 a 5,3 s), ou tombe tout
# le segment de "deux" [2.967, 5.239], et 270..323 (9,0 a 10,8 s), ou tombent
# le teaser [9.221, 10.637] et le segment de "quatre".
TROUS_VIDEO = ((87, 158), (270, 323))


def test_a_piece_inside_a_video_gap_holds_the_last_frame_before_it(tmp_path, numbered_cut_source):
    """Revue A2, fix round 2 : un morceau sans aucune image source (tout entier
    dans un trou video, l'audio continuant) ne sortait rien ; concat recollait
    la suite plus tot et tout ce qui suivait jouait decale (600 images au lieu
    de 642, -700 ms de desynchro jusqu'a la fin ; teaser vide : tout le clip
    decale de 1,4 s). Chaque morceau lit desormais sa source une seconde avant
    son debut, sans remonter avant le debut du clip : un morceau dans un trou
    tient la derniere image qui le precede, et rien ne se decale."""
    source = numbered_cut_source("mp4", 30, TROUS_VIDEO)
    out, morceaux, _, rangs, jeu = _rendu_du_scenario_de_grille(tmp_path, source, 30.0, 30)
    absentes = {n for a, b in TROUS_VIDEO for n in range(a, b + 1)}
    presentes = [n for n in range(round(GRID_DUR_S * 30)) if n not in absentes]
    vides = 0
    for k, m in enumerate(morceaux):
        vues = jeu[rangs[k]:rangs[k + 1]]
        fenetre = [n for n in presentes if m.src_start - 1e-6 <= n / 30 < m.src_end - 1e-6]
        avant = max((n for n in presentes if n / 30 < m.src_start - 1e-6), default=None)
        if not fenetre:
            vides += 1
            assert set(vues) == {avant}, (
                f"{out}: morceau {k} {m}, sans image source, montre {vues} au lieu de tenir "
                f"l'image {avant}")
        else:
            assert set(vues[1:]) <= set(fenetre) and vues[0] in {avant, *fenetre}, (
                f"{out}: morceau {k} {m} montre {vues}, fenetre {fenetre[0]}..{fenetre[-1]}")
    assert vides == 3, f"le scenario ne mord pas : {vides} morceaux sans image, {morceaux}"


# --------------------------------------------------------------------------
# Case 3d (lot A2, fix round 1) : aucune coupe (clip sans mots, EDL plein) mais
# zoompan actif, pour un pic audio en fin de clip.
# --------------------------------------------------------------------------

UNCUT_START_S, UNCUT_END_S = 1.0, 15.0
UNCUT_SIGNALS = [(float(t), 0.9 if t == 14 else 0.1) for t in range(16)]


@pytest.mark.parametrize("conteneur, cadence, fps_vod, grille", [
    ("mkv", 60, 60.0, Fraction(60)),
    ("mp4", "30000/1001", 29.97002997002997, Fraction(30000, 1001))])
def test_cam_and_game_show_the_same_frame_without_cuts_under_zoompan(
        tmp_path, numbered_cut_source, conteneur, cadence, fps_vod, grille):
    """Revue A2, fix round 1 : sans coupe, la branche jeu gardait les instants
    de la source apres -ss et zoompan re-datait la branche cam sur sa grille ;
    cam et jeu divergeaient (420 images sur 422 a 29,97 ips, 563 sur 846 sur
    un mkv a 60 ips). Avant l'attaque du punch-in (le zoom de 1,22 deplacerait
    les bandes de la zone cam), cam et jeu montrent le meme numero, chaque
    image source une fois, et le rendu compte ceil(duree * grille) images."""
    source = numbered_cut_source(conteneur, cadence)
    clip_id = _seed_source_clip(tmp_path / "clipforge.db", source=source, width=1920,
                                height=1080, fps=fps_vod, words=[], start_s=UNCUT_START_S,
                                end_s=UNCUT_END_S, montage="1", signals=UNCUT_SIGNALS)
    _rendre_source(tmp_path, clip_id)
    clip = _clip_row(tmp_path, clip_id)
    assert clip["status"] == "done", f"rendu sans coupe echoue : {clip['error']}"

    fenetres = detect_punches(UNCUT_SIGNALS, clip_start=UNCUT_START_S, clip_end=UNCUT_END_S)
    assert len(fenetres) == 1, fenetres
    avant_zoom = int((fenetres[0].start - ATTACK_S - 0.1) * grille)
    out = Path(clip["output_path"])
    cam = _rendered_frame_numbers(out, "cam")[:avant_zoom]
    jeu = _rendered_frame_numbers(out, "jeu")
    differentes = [i for i, (c, j) in enumerate(zip(cam, jeu)) if c != j]
    assert avant_zoom > 10 * grille and not differentes, (
        f"{out}: cam et jeu different sur {len(differentes)} images sur {avant_zoom} "
        f"(premieres : {[(i, cam[i], jeu[i]) for i in differentes[:6]]})")
    attendues = -(-round((UNCUT_END_S - UNCUT_START_S) * 1000) * grille // 1000)
    assert len(jeu) == attendues, f"{out}: {len(jeu)} images, attendu {attendues}"
    pas = [b - a for a, b in pairwise(jeu[:avant_zoom])]
    assert set(pas) == {1}, f"{out}: pas entre images {sorted(set(pas))} : {jeu[:avant_zoom]}"
