import json
import re
import subprocess
from pathlib import Path

import pytest

from clipforge.models import SubtitleStyle
from clipforge.pipeline.probe import probe_media
from clipforge.pipeline.segmenter import Word
from clipforge.render.captions_pop import build_pop_ass
from clipforge.render.ffmpeg_cmd import build_render_cmd, even
from clipforge.render.filtergraph import audio_cut, teaser_input_args, video_cut
from clipforge.render.montage.edl import Edl, Segment
from clipforge.render.montage.motion import PunchWindow, legacy_zoom_expression
from clipforge.render.sfx import audio_filter, emoji_path, sfx_plan

PROFILE = {"cam_x": 25, "cam_y": 801, "cam_w": 479, "cam_h": 257,
           "game_x": 525, "game_y": 1, "game_w": 869, "game_h": 1079}

FIXTURE = Path(__file__).parent / "fixtures" / "fixture.mp4"
FIXTURE_PROFILE = {"cam_x": 0, "cam_y": 0, "cam_w": 640, "cam_h": 340,
                   "game_x": 640, "game_y": 0, "game_w": 868, "game_h": 1080}


def test_even_rounds_down():
    assert even(479) == 478 and even(480) == 480


def test_build_render_cmd_structure():
    cmd = build_render_cmd(Path(r"C:\v\src.mp4"), Path("out.mp4"), "captions.ass",
                           start_s=12.5, end_s=47.5, profile=PROFILE, fps=30.0,
                           use_nvenc=True)
    assert cmd[0] == "ffmpeg"
    i = cmd.index("-ss")
    assert cmd[i + 1] == "12.5" and cmd[cmd.index("-t") + 1] == "35.0"
    assert cmd.index("-ss") < cmd.index("-i")  # seek precis avant -i
    fc = cmd[cmd.index("-filter_complex") + 1]
    assert "crop=478:256:25:801" in fc          # dimensions rendues paires
    assert "scale=1080:576" in fc and "scale=1080:1344" in fc
    assert "vstack=inputs=2" in fc
    assert "subtitles=captions.ass" in fc       # chemin RELATIF (cwd = dossier job)
    assert "h264_nvenc" in cmd and "yuv420p" in cmd


def test_build_render_cmd_cpu_fallback():
    cmd = build_render_cmd(Path("s.mp4"), Path("o.mp4"), "c.ass", start_s=0, end_s=30,
                           profile=PROFILE, fps=30.0, use_nvenc=False)
    assert "libx264" in cmd and "h264_nvenc" not in cmd


def test_cmd_inserts_zoompan_on_the_cam_branch_with_source_fps():
    cmd = build_render_cmd(Path("s.mp4"), Path("o.mp4"), "c.ass", start_s=10, end_s=30,
                           profile=PROFILE, fps=24.0, zoom_expr="1+0.22*(between(it,3,4))")
    fc = cmd[cmd.index("-filter_complex") + 1]
    assert "zoompan=z='1+0.22*(between(it,3,4))':d=1" in fc
    assert "fps=24.0" in fc and "s=1080x576" in fc
    assert fc.index("crop=") < fc.index("zoompan=") < fc.index("vstack=")


def test_no_punch_means_no_zoompan():
    cmd = build_render_cmd(Path("s.mp4"), Path("o.mp4"), "c.ass", start_s=0, end_s=20,
                           profile=PROFILE, fps=24.0, zoom_expr="1")
    assert "zoompan" not in " ".join(cmd)


def test_emoji_is_overlaid_after_subtitles_and_gated_in_time():
    cmd = build_render_cmd(Path("s.mp4"), Path("o.mp4"), "c.ass", start_s=0, end_s=20,
                           profile=PROFILE, fps=24.0,
                           emoji=(Path("e.png"), 0.0, 2.8))
    fc = cmd[cmd.index("-filter_complex") + 1]
    assert "overlay" in fc and "enable='between(t,0.000,2.800)'" in fc
    assert fc.index("subtitles=") < fc.index("overlay")
    assert cmd.count("-i") == 2                       # source + png
    # `-loop 1` rend le PNG infini : sans shortest=1, ffmpeg n'atteint JAMAIS la
    # fin du rendu (mesure : toujours vivant a 45 s sur un clip de 2 s). Le
    # garde-fou de queue.py (RENDER_TIMEOUT_S) borne la casse, cette assertion
    # l'empeche.
    assert "shortest=1" in fc


def test_audio_graph_replaces_the_default_audio_map():
    cmd = build_render_cmd(Path("s.mp4"), Path("o.mp4"), "c.ass", start_s=0, end_s=20,
                           profile=PROFILE, fps=24.0,
                           sfx_inputs=["-i", "w.wav"], audio_graph="[0:a]anull[aout]")
    joined = " ".join(cmd)
    assert "[aout]" in joined and "0:a?" not in joined


def test_without_audio_graph_the_source_track_stays_optional():
    cmd = build_render_cmd(Path("s.mp4"), Path("o.mp4"), "c.ass", start_s=0, end_s=20,
                           profile=PROFILE, fps=24.0)
    assert "0:a?" in cmd and "[aout]" not in cmd


def test_emoji_input_index_counts_the_sfx_inputs_before_it():
    """Le PNG entre APRES les effets : `[0:a]`, `[1:a]`… du graphe audio sont
    numerotes par audio_filter, un emoji insere avant eux les decalerait tous.
    """
    cmd = build_render_cmd(Path("s.mp4"), Path("o.mp4"), "c.ass", start_s=0, end_s=20,
                           profile=PROFILE, fps=24.0, emoji=(Path("e.png"), 0.0, 2.8),
                           sfx_inputs=["-i", "impact.wav", "-i", "whoosh.wav"],
                           audio_graph="[0:a][1:a][2:a]amix=inputs=3[aout]")
    fc = cmd[cmd.index("-filter_complex") + 1]
    assert "[3:v]scale=160:-1" in fc
    assert cmd.index("e.png") > cmd.index("whoosh.wav")


def test_sfx_inputs_come_right_after_the_source():
    cmd = build_render_cmd(Path("s.mp4"), Path("o.mp4"), "c.ass", start_s=0, end_s=20,
                           profile=PROFILE, fps=24.0, sfx_inputs=["-i", "impact.wav"],
                           audio_graph="[0:a][1:a]amix=inputs=2[aout]")
    assert cmd[cmd.index("s.mp4") + 1: cmd.index("s.mp4") + 3] == ["-i", "impact.wav"]


VIDEO_CUT = ("[0:v]trim=start=0.000:end=4.000,setpts=PTS-STARTPTS[vc0];"
            "[0:v]trim=start=6.000:end=9.000,setpts=PTS-STARTPTS[vc1];"
            "[vc0][vc1]concat=n=2:v=1:a=0[vcut];[vcut]split=2[vcuta][vcutb]")
AUDIO_CUT = ("[0:a]atrim=start=0.000:end=4.000,asetpts=PTS-STARTPTS[ac0];"
            "[0:a]atrim=start=6.000:end=9.000,asetpts=PTS-STARTPTS[ac1];"
            "[ac0][ac1]concat=n=2:v=0:a=1[acut]")


def test_build_render_cmd_with_defaults_matches_the_v5_golden_untouched():
    """Golden V5 : les nouveaux kwargs (video_cut/audio_cut/x_expr/y_expr) ont
    des defauts qui laissent la commande OCTET POUR OCTET identique a avant
    leur introduction (Task 7)."""
    cmd = build_render_cmd(Path(r"C:\v\src.mp4"), Path("out.mp4"), "captions.ass",
                           start_s=12.5, end_s=47.5, profile=PROFILE, fps=30.0,
                           use_nvenc=True)
    assert cmd[0] == "ffmpeg"
    i = cmd.index("-ss")
    assert cmd[i + 1] == "12.5" and cmd[cmd.index("-t") + 1] == "35.0"
    fc = cmd[cmd.index("-filter_complex") + 1]
    assert "crop=478:256:25:801" in fc
    assert "scale=1080:576" in fc and "scale=1080:1344" in fc
    assert "vstack=inputs=2" in fc
    assert "subtitles=captions.ass" in fc
    assert "vcuta" not in fc and "vcutb" not in fc and "acut" not in fc


def test_build_render_cmd_with_cuts_prefixes_graph_and_reads_split_labels():
    cmd = build_render_cmd(Path("s.mp4"), Path("o.mp4"), "c.ass", start_s=0.0, end_s=20.0,
                           profile=PROFILE, fps=24.0, video_cut=VIDEO_CUT, audio_cut=AUDIO_CUT,
                           sfx_inputs=["-i", "impact.wav"],
                           audio_graph="[acut][1:a]amix=inputs=2[aout]")
    fc = cmd[cmd.index("-filter_complex") + 1]
    assert "split=2[vcuta][vcutb]" in fc
    for label in ("[vcuta]", "[vcutb]", "[acut]"):
        assert fc.count(f"{label}") >= 1
    # producteurs : vcuta/vcutb via le seul split, acut via le seul concat audio
    assert fc.count("split=2[vcuta][vcutb]") == 1
    assert fc.count("v=0:a=1[acut]") == 1
    # les branches cam/jeu lisent le graphe de coupe, jamais l'entree brute
    assert "[vcuta]crop=" in fc and "[vcutb]crop=" in fc
    # la duree lue en entree reste celle du clip source (les coupes raccourcissent
    # la sortie, pas la lecture) : voir docstring build_render_cmd/filtergraph.
    assert cmd[cmd.index("-t") + 1] == "20.0"


TEASER_INPUT = ["-ss", "41.846", "-t", "1.5", "-i", "s.mp4"]
VIDEO_CUT_TEASER = ("[3:v]trim=end=1.500,setpts=PTS-STARTPTS[vc0];"
                    "[0:v]trim=start=0.000:end=4.000,setpts=PTS-STARTPTS[vc1];"
                    "[vc0][vc1]concat=n=2:v=1:a=0[vcut];[vcut]split=2[vcuta][vcutb]")
AUDIO_CUT_TEASER = ("[3:a]asetpts=PTS-STARTPTS[ac0];"
                    "[0:a]atrim=start=0.000:end=4.000,asetpts=PTS-STARTPTS[ac1];"
                    "[ac0][ac1]concat=n=2:v=0:a=1[acut]")


def test_teaser_input_sits_after_the_sfx_and_before_the_emoji():
    """L'entree teaser vient juste apres les effets : son index (1 + nb d'effets)
    est alors le meme que dans la commande de mesure, qui n'a pas d'emoji. Le
    PNG passe apres elle et son index doit la compter."""
    cmd = build_render_cmd(Path("s.mp4"), Path("o.mp4"), "c.ass", start_s=0.0, end_s=20.0,
                           profile=PROFILE, fps=24.0, emoji=(Path("e.png"), 0.0, 2.8),
                           sfx_inputs=["-i", "impact.wav", "-i", "whoosh.wav"],
                           audio_graph="[acut][1:a][2:a]amix=inputs=3[aout]",
                           video_cut=VIDEO_CUT_TEASER, audio_cut=AUDIO_CUT_TEASER,
                           teaser_input=TEASER_INPUT)
    entrees = [i for i, arg in enumerate(cmd) if arg == "-i"]
    assert [cmd[i + 1] for i in entrees] == ["s.mp4", "impact.wav", "whoosh.wav", "s.mp4",
                                             "e.png"]
    assert cmd[entrees[3] - 4: entrees[3] + 2] == TEASER_INPUT
    fc = cmd[cmd.index("-filter_complex") + 1]
    assert "[4:v]scale=160:-1" in fc


_LABEL_RE = re.compile(r"\[([^\]]+)\]")
_ENTREE_RE = re.compile(r"\d+:[va]")


def _labels(fc: str) -> tuple[list[str], list[str]]:
    """(lus, produits) d'un filter_complex : labels en tete et en queue de
    chaque chaine."""
    lus, produits = [], []
    for chaine in fc.split(";"):
        lus += _LABEL_RE.findall(re.match(r"(?:\[[^\]]+\])*", chaine).group(0))
        produits += _LABEL_RE.findall(re.search(r"(?:\[[^\]]+\])*$", chaine).group(0))
    return lus, produits


@pytest.mark.parametrize("teaser", [None, Segment(4.0, 5.5)])
def test_every_cut_label_has_one_producer_and_one_reader(teaser):
    """Revue finale V6 : le contrat des labels vcuta/vcutb/acut entre
    filtergraph, sfx et build_render_cmd n'etait verifie qu'en integration.
    Graphes REELS, avec et sans teaser, effets et emoji compris : chaque label
    lu (hors entrees [n:v]/[n:a]) a un producteur et un seul, chaque label
    produit est lu une fois, sauf [v] et [aout] que la commande mappe."""
    edl = Edl(teaser, [Segment(0.0, 3.0), Segment(6.0, 9.4)])
    events = sfx_plan([PunchWindow(7.0, 8.4)], with_hook=True,
                      teaser_cut_s=1.5 if teaser else None)
    index = 1 + len(events) if teaser else None
    entree_teaser = (teaser_input_args(Path("s.mp4"), start_s=5.0, teaser=teaser)
                     if teaser else None)
    sfx_inputs, audio_graph = audio_filter(events, measured=None, src="[acut]")
    cmd = build_render_cmd(
        Path("s.mp4"), Path("o.mp4"), "c.ass", start_s=5.0, end_s=15.0, profile=PROFILE,
        fps="30/1", zoom_expr="1+0.1*between(it,1,2)", emoji=(Path("e.png"), 0.0, 2.8),
        sfx_inputs=sfx_inputs, audio_graph=audio_graph,
        video_cut=video_cut(edl, clip_dur_s=10.0, fps=30.0, teaser_index=index),
        audio_cut=audio_cut(edl, clip_dur_s=10.0, teaser_index=index),
        teaser_input=entree_teaser)
    fc = cmd[cmd.index("-filter_complex") + 1]
    lus, produits = _labels(fc)
    internes = [label for label in lus if not _ENTREE_RE.fullmatch(label)]
    assert {"vcuta", "vcutb", "acut"} <= set(internes), fc
    for label in internes:
        assert produits.count(label) == 1, (label, fc)
    for label in produits:
        assert lus.count(label) == (0 if label in ("v", "aout") else 1), (label, fc)
    assert produits.count("v") == produits.count("aout") == 1, fc
    assert cmd[cmd.index("-map") + 1] == "[v]" and "[aout]" in cmd


def test_build_render_cmd_x_y_expr_override_the_zoompan_defaults():
    cmd = build_render_cmd(Path("s.mp4"), Path("o.mp4"), "c.ass", start_s=0, end_s=20,
                           profile=PROFILE, fps=24.0, zoom_expr="1+0.1*between(it,1,2)",
                           x_expr="iw/2-(iw/zoom/2)+5", y_expr="ih*0.5-(ih/zoom*0.5)")
    fc = cmd[cmd.index("-filter_complex") + 1]
    assert "x='iw/2-(iw/zoom/2)+5'" in fc
    assert "y='ih*0.5-(ih/zoom*0.5)'" in fc


def test_graph_file_carries_the_filter_complex_out_of_the_command_line(tmp_path):
    """Revue A2 (integration M1) : chaque segment coupe allonge le graphe
    d'environ 280 caracteres ; en ligne, la commande depassait la limite de
    CreateProcess (32 767) vers 105 segments (WinError 206). Le graphe part
    dans un fichier lu par -/filter_complex, le reste de la commande ne change
    pas."""
    kwargs = {"start_s": 12.5, "end_s": 47.5, "profile": PROFILE, "fps": "30/1",
              "zoom_expr": "1+0.1*between(it,1,2)", "video_cut": VIDEO_CUT,
              "audio_cut": AUDIO_CUT, "use_nvenc": False}
    en_ligne = build_render_cmd(Path("s.mp4"), Path("o.mp4"), "c.ass", **kwargs)
    fichier = tmp_path / "filter_complex.txt"
    cmd = build_render_cmd(Path("s.mp4"), Path("o.mp4"), "c.ass", graph_file=fichier, **kwargs)
    i = en_ligne.index("-filter_complex")
    assert cmd == [*en_ligne[:i], "-/filter_complex", str(fichier), *en_ligne[i + 2:]]
    assert fichier.read_text(encoding="utf-8") == en_ligne[i + 1]


def test_build_render_cmd_rejects_video_cut_without_audio_cut():
    """Finding 3 (review Task 7) : video_cut seul rend une video coupee sur une
    piste audio pleine longueur -- desync silencieuse (mesure : video 5 s, audio
    16 s), rc=0. Rien ne validait les deux kwargs l'un contre l'autre."""
    with pytest.raises(ValueError, match="video_cut"):
        build_render_cmd(Path("s.mp4"), Path("o.mp4"), "c.ass", start_s=0.0, end_s=20.0,
                         profile=PROFILE, fps=24.0, video_cut=VIDEO_CUT, audio_cut="")


def test_build_render_cmd_rejects_audio_cut_without_video_cut():
    with pytest.raises(ValueError, match="audio_cut"):
        build_render_cmd(Path("s.mp4"), Path("o.mp4"), "c.ass", start_s=0.0, end_s=20.0,
                         profile=PROFILE, fps=24.0, video_cut="", audio_cut=AUDIO_CUT)


def _audio_stream(path: Path) -> dict:
    """Codec/canaux/sample rate de la premiere piste audio, lus par ffprobe via
    Python (jamais un shell). `channels` et `sample_rate` portent D1/D2 (Task 10)."""
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries",
         "stream=codec_name,channels,sample_rate", "-print_format", "json", str(path)],
        capture_output=True, text=True, encoding="utf-8", check=True)
    return json.loads(proc.stdout)["streams"][0]


@pytest.mark.integration
def test_full_render_on_fixture(tmp_path):
    """Montage complet sur la fixture : pop, punch-in, emoji et audio mixe."""
    words = [Word(start=6.0, end=6.5, text="test"), Word(start=6.5, end=7.2, text="rendu")]
    (tmp_path / "captions.ass").write_text(
        build_pop_ass(words, SubtitleStyle(), clip_start_s=5.0, clip_dur_s=10.0,
                      hook="IL MANQUE DE MOURIR", handle="twitch.tv/pseudo"),
        encoding="utf-8")
    punches = [PunchWindow(3.0, 4.4)]
    events = sfx_plan(punches, with_hook=True)
    sfx_inputs, audio_graph = audio_filter(events, measured=None)
    cmd = build_render_cmd(FIXTURE, tmp_path / "out.mp4", "captions.ass",
                           start_s=5.0, end_s=15.0, profile=FIXTURE_PROFILE, fps=30.0,
                           zoom_expr=legacy_zoom_expression(punches),
                           emoji=(emoji_path("💀"), 0.0, 2.8),
                           sfx_inputs=sfx_inputs, audio_graph=audio_graph,
                           use_nvenc=False)  # CPU : le test passe partout
    proc = subprocess.run(cmd, cwd=tmp_path, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", check=False)
    assert proc.returncode == 0, proc.stderr[-600:]
    info = probe_media(tmp_path / "out.mp4")
    assert (info.width, info.height) == (1080, 1920)
    assert 9.8 <= info.duration_s <= 10.2
    audio = _audio_stream(tmp_path / "out.mp4")
    assert audio["codec_name"] == "aac"
    # D1/D2 (Task 10) : fixture.mp4 est MONO en source (44100 Hz 1ch, verifie via
    # ffprobe hors-test) -- amix force quand meme la sortie en stereo des qu'une
    # branche SFX est aformat=stereo (ici impact+whoosh, with_hook=True), verifie
    # empiriquement avant ce fix. channels==2 sur CE fixture mono est donc une
    # preuve plus forte que sur une source deja stereo : la stereo vient bien du
    # graphe, pas d'un simple pass-through de la source.
    assert audio["channels"] == 2
    assert audio["sample_rate"] == "48000"
