import json
import subprocess
import wave
from pathlib import Path

import pytest

from clipforge.render.montage.motion import PunchWindow
from clipforge.render.sfx import (
    ASSETS_DIR,
    EMOJI_DIR,
    SFX_DIR,
    SfxEvent,
    audio_filter,
    emoji_path,
    measure_loudness,
    sfx_plan,
)

FIXTURE = Path(__file__).parent / "fixtures" / "fixture.mp4"


def test_the_twenty_emojis_are_vendored_and_resolvable():
    assert emoji_path("💀") is not None and emoji_path("💀").is_file()
    assert emoji_path("🚀") is None                      # hors du jeu : refuse, ne casse pas


def test_all_twenty_vendored_emojis_resolve_to_valid_png_files():
    from clipforge.render.sfx import VENDORED_EMOJI, emoji_clusters
    clusters = emoji_clusters(VENDORED_EMOJI)
    assert len(clusters) == 20
    for char in clusters:
        path = emoji_path(char)
        assert path is not None and path.is_file(), f"emoji {char!r} non resolu"
        data = path.read_bytes()
        # 400 : le plus petit PNG reel du jeu (l'eclair) pese 446 o, une page
        # d'erreur GitHub sauvee en .png pese 14 o — large marge des deux cotes.
        assert len(data) > 400 and data.startswith(b"\x89PNG")


def test_assets_resolve_within_the_installed_package():
    import clipforge
    pkg_root = Path(clipforge.__file__).resolve().parent
    assert SFX_DIR.is_relative_to(pkg_root)
    assert EMOJI_DIR.is_relative_to(pkg_root)


def test_attribution_credits_twemoji_under_cc_by():
    texte = (ASSETS_DIR / "ATTRIBUTION.md").read_text(encoding="utf-8")
    assert "Twemoji" in texte and "CC-BY 4.0" in texte


def test_whoosh_and_impact_are_44100hz_mono_16bit_wav():
    for name in ("whoosh.wav", "impact.wav"):
        with wave.open(str(SFX_DIR / name)) as w:
            assert w.getframerate() == 44100
            assert w.getnchannels() == 1
            assert w.getsampwidth() == 2
            assert w.getnframes() > 0


def test_sfx_plan_caps_and_orders():
    punches = [PunchWindow(3.0, 4.4), PunchWindow(12.0, 13.4),
               PunchWindow(20.0, 21.4), PunchWindow(28.0, 29.4)]
    plan = sfx_plan(punches, with_hook=True, max_sfx=4)
    assert len(plan) == 4                                # impact + 3 whoosh, plafond tenu
    assert plan[0].at_s == 0.05 and "impact" in plan[0].path.name
    assert all(e.gain_db <= -10 for e in plan)


def test_sfx_plan_without_hook_omits_impact():
    plan = sfx_plan([PunchWindow(1.0, 2.0)], with_hook=False)
    assert len(plan) == 1
    assert all("impact" not in e.path.name for e in plan)


def test_sfx_plan_clamps_lead_time_to_zero():
    plan = sfx_plan([PunchWindow(0.05, 1.0)], with_hook=False)
    assert plan[0].at_s == 0.0


def test_sfx_plan_empty_without_punches_or_hook():
    assert sfx_plan([], with_hook=False) == []


def test_sfx_plan_teaser_cut_places_a_whoosh_before_the_cut_point():
    plan = sfx_plan([], with_hook=False, teaser_cut_s=1.5)
    assert len(plan) == 1
    assert plan[0].at_s == pytest.approx(1.35)          # 1.5 - WHOOSH_LEAD_S (0.15)
    assert "whoosh" in plan[0].path.name


def test_sfx_plan_teaser_whoosh_survives_truncation_right_after_the_hook():
    """max_sfx=4 : impact + teaser whoosh doivent survivre a la troncature meme
    avec 3 punches qui, seuls, rempliraient deja le quota."""
    punches = [PunchWindow(3.0, 4.4), PunchWindow(12.0, 13.4), PunchWindow(20.0, 21.4)]
    plan = sfx_plan(punches, with_hook=True, teaser_cut_s=1.5, max_sfx=4)
    assert len(plan) == 4
    assert "impact" in plan[0].path.name
    assert plan[1].at_s == pytest.approx(1.35) and "whoosh" in plan[1].path.name


def test_sfx_plan_without_teaser_cut_is_unchanged():
    plan = sfx_plan([PunchWindow(3.0, 4.4)], with_hook=False)
    assert len(plan) == 1 and plan[0].at_s == pytest.approx(2.85)


def test_audio_filter_builds_amix_and_linear_loudnorm():
    ev = [SfxEvent(0.05, SFX_DIR / "impact.wav", -12.0)]
    inputs, graphe = audio_filter(ev, measured={"input_i": "-23.1", "input_tp": "-4.0",
                                                "input_lra": "6.5", "input_thresh": "-33.5",
                                                "target_offset": "0.3"})
    assert inputs == ["-i", str(SFX_DIR / "impact.wav")]
    assert "adelay=50|50" in graphe and "amix=inputs=2:normalize=0" in graphe
    assert "measured_I=-23.1" in graphe and "linear=true" in graphe and "[aout]" in graphe


def test_audio_filter_without_sfx_still_normalizes():
    inputs, graphe = audio_filter([], measured=None)
    assert inputs == [] and graphe.startswith("[0:a]loudnorm=I=-14") and "[aout]" in graphe


def test_audio_filter_orders_multiple_inputs_by_event_order():
    ev = [SfxEvent(0.0, SFX_DIR / "impact.wav", -12.0),
          SfxEvent(1.0, SFX_DIR / "whoosh.wav", -14.0)]
    inputs, graphe = audio_filter(ev, measured=None)
    assert inputs == ["-i", str(SFX_DIR / "impact.wav"), "-i", str(SFX_DIR / "whoosh.wav")]
    assert "[1:a]adelay=0|0" in graphe and "[2:a]adelay=1000|1000" in graphe
    assert "amix=inputs=3:normalize=0" in graphe


def test_audio_filter_amix_anchors_duration_to_the_voice_track():
    """Regression : amix dure=longest par defaut. Un whoosh pres de la fin du clip
    (ex. punch a clip_end) rallongerait [aout] au-dela de la voix/video — Task 5
    injecte ce graphe tel quel dans le rendu, donc un decalage audio/video muet,
    sans test qui echoue. duration=first ancre [aout] sur [0:a] (la voix).
    """
    ev = [SfxEvent(29.65, SFX_DIR / "whoosh.wav", -14.0)]
    _, graphe = audio_filter(ev, measured=None)
    assert "amix=inputs=2:normalize=0:duration=first" in graphe


def test_audio_filter_without_sfx_has_no_amix_clause():
    """Le chemin sans effet court-circuite amix : rien a ancrer, rien a regresser."""
    _, graphe = audio_filter([], measured=None)
    assert "amix" not in graphe


def test_audio_filter_forces_stereo_on_each_sfx_input():
    """D1 : les WAV embarques sont mono (44100 Hz 1ch, cf. test dedie plus haut).
    Sans aformat=stereo sur chaque branche, amix negocie le plus petit
    denominateur commun de channel layout et rabat la voix stereo sur un seul
    canal en sortie -- exactement la regression mesuree sur les clips V5."""
    ev = [SfxEvent(0.05, SFX_DIR / "impact.wav", -12.0)]
    _, graphe = audio_filter(ev, measured=None)
    assert "adelay=50|50,aformat=channel_layouts=stereo,volume=-12.0dB" in graphe


def test_audio_filter_forces_stereo_on_every_sfx_input_when_several():
    ev = [SfxEvent(0.0, SFX_DIR / "impact.wav", -12.0),
          SfxEvent(1.0, SFX_DIR / "whoosh.wav", -14.0)]
    _, graphe = audio_filter(ev, measured=None)
    assert "[1:a]adelay=0|0,aformat=channel_layouts=stereo,volume=-12.0dB[sfx1]" in graphe
    assert "[2:a]adelay=1000|1000,aformat=channel_layouts=stereo,volume=-14.0dB[sfx2]" in graphe


def test_audio_filter_resamples_to_48000_after_loudnorm_with_events():
    """D2 : loudnorm annonce un sample rate de sortie (parfois 192 kHz) qui ne
    correspond pas a l'entree ; sans aresample explicite, l'encodeur AAC s'y
    ajuste et livre du 96 kHz au lieu de 48 -- regression mesuree sur les clips V5."""
    ev = [SfxEvent(0.05, SFX_DIR / "impact.wav", -12.0)]
    _, graphe = audio_filter(ev, measured=None)
    assert graphe.endswith(",aresample=48000[aout]")
    assert graphe.index("loudnorm=I=-14") < graphe.index("aresample=48000")


def test_audio_filter_resamples_to_48000_after_loudnorm_without_events():
    """Meme garantie sur la voie sans SFX (clip utilisateur sans punch ni hook) :
    une seule fonction partagee (_mix_graph), donc un seul endroit a corriger."""
    _, graphe = audio_filter([], measured=None)
    assert graphe == "[0:a]loudnorm=I=-14:TP=-1:LRA=11,aresample=48000[aout]"


def test_audio_filter_resamples_to_48000_on_the_measured_linear_path_too():
    """D2 doit tenir sur les deux chemins de loudnorm : dynamique degrade (ci-dessus)
    ET lineaire une passe avec des stats mesurees (measured != None)."""
    ev = [SfxEvent(0.05, SFX_DIR / "impact.wav", -12.0)]
    _, graphe = audio_filter(ev, measured={"input_i": "-23.1", "input_tp": "-4.0",
                                           "input_lra": "6.5", "input_thresh": "-33.5",
                                           "target_offset": "0.3"})
    assert graphe.endswith(",aresample=48000[aout]")
    assert "linear=true" in graphe


def test_measure_loudness_returns_none_when_ffmpeg_missing(monkeypatch, caplog):
    def fake_run(*a, **k):
        raise FileNotFoundError("ffmpeg introuvable")
    monkeypatch.setattr(subprocess, "run", fake_run)
    with caplog.at_level("WARNING"):
        assert measure_loudness(FIXTURE, start_s=0.0, end_s=5.0, events=[]) is None
    assert "loudness" in caplog.text.lower()


def test_measure_loudness_returns_none_on_timeout(monkeypatch):
    def fake_run(*a, **k):
        raise subprocess.TimeoutExpired(cmd=["ffmpeg"], timeout=120)
    monkeypatch.setattr(subprocess, "run", fake_run)
    assert measure_loudness(FIXTURE, start_s=0.0, end_s=5.0, events=[]) is None


def test_measure_loudness_returns_none_on_nonzero_exit(monkeypatch):
    def fake_run(*a, **k):
        return subprocess.CompletedProcess(args=["ffmpeg"], returncode=1, stdout="", stderr="boom")
    monkeypatch.setattr(subprocess, "run", fake_run)
    assert measure_loudness(FIXTURE, start_s=0.0, end_s=5.0, events=[]) is None


def test_measure_loudness_returns_none_on_unparsable_output(monkeypatch):
    def fake_run(*a, **k):
        return subprocess.CompletedProcess(args=["ffmpeg"], returncode=0, stdout="",
                                           stderr="rien a analyser ici")
    monkeypatch.setattr(subprocess, "run", fake_run)
    assert measure_loudness(FIXTURE, start_s=0.0, end_s=5.0, events=[]) is None


LOUDNORM_STDERR = """[Parsed_loudnorm_0 @ 0000000000000000]
{
\t"input_i" : "-23.71",
\t"input_tp" : "-4.35",
\t"input_lra" : "6.10",
\t"input_thresh" : "-34.02",
\t"output_i" : "-14.00",
\t"output_tp" : "-1.00",
\t"output_lra" : "6.10",
\t"output_thresh" : "-24.00",
\t"normalization_type" : "dynamic",
\t"target_offset" : "0.20"
}
"""


def test_measure_loudness_parses_the_json_block(monkeypatch):
    captured = {}

    def fake_run(cmd, **k):
        captured["cmd"] = cmd
        captured["kwargs"] = k
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="",
                                           stderr=LOUDNORM_STDERR)
    monkeypatch.setattr(subprocess, "run", fake_run)
    mesure = measure_loudness(FIXTURE, start_s=2.0, end_s=7.0, events=[])
    assert mesure == json.loads(LOUDNORM_STDERR.split("\n", 1)[1])
    assert "print_format=json" in captured["cmd"][captured["cmd"].index("-filter_complex") + 1]
    assert captured["kwargs"]["timeout"] == 120


@pytest.mark.parametrize("cle, valeur", [("input_i", "-inf"), ("target_offset", "inf"),
                                          ("input_tp", "nan"), ("input_lra", "abc")])
def test_measure_loudness_rejects_a_non_finite_measure(monkeypatch, caplog, cle, valeur):
    """Revue finale V6 (lot C, C4) : sur du silence numerique, loudnorm rend
    input_i "-inf" et target_offset "inf" (ffmpeg 9.0.1) ; la seconde passe
    refuse alors measured_I (Result too large) et le clip echoue, la ou le
    repli une passe l'aurait rendu."""
    stderr = LOUDNORM_STDERR.replace(
        f'"{cle}" : "{json.loads(LOUDNORM_STDERR.split(chr(10), 1)[1])[cle]}"',
        f'"{cle}" : "{valeur}"')
    assert f'"{valeur}"' in stderr

    def fake_run(cmd, **k):
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr=stderr)
    monkeypatch.setattr(subprocess, "run", fake_run)
    with caplog.at_level("WARNING"):
        assert measure_loudness(FIXTURE, start_s=0.0, end_s=5.0, events=[]) is None
    assert cle in caplog.text and valeur in caplog.text, caplog.text


def test_measure_loudness_graph_anchors_duration_when_events_present(monkeypatch):
    """_mix_graph est partage entre audio_filter et measure_loudness : la premiere
    passe de mesure doit ancrer amix sur la voix, exactement comme le rendu final.
    """
    captured = {}

    def fake_run(cmd, **k):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="",
                                           stderr=LOUDNORM_STDERR)
    monkeypatch.setattr(subprocess, "run", fake_run)
    ev = [SfxEvent(0.05, SFX_DIR / "impact.wav", -12.0)]
    measure_loudness(FIXTURE, start_s=0.0, end_s=5.0, events=ev)
    graphe = captured["cmd"][captured["cmd"].index("-filter_complex") + 1]
    assert "amix=inputs=2:normalize=0:duration=first" in graphe


@pytest.mark.integration
def test_measure_loudness_on_real_fixture_with_ffmpeg():
    mesure = measure_loudness(FIXTURE, start_s=0.0, end_s=10.0, events=[])
    assert mesure is not None
    assert {"input_i", "input_tp", "input_lra", "input_thresh", "target_offset"} <= mesure.keys()


AUDIO_CUT_GRAPH = ("[0:a]atrim=start=0.000:end=4.000,asetpts=PTS-STARTPTS[ac0];"
                  "[0:a]atrim=start=6.000:end=9.000,asetpts=PTS-STARTPTS[ac1];"
                  "[ac0][ac1]concat=n=2:v=0:a=1[acut]")


def test_measure_loudness_with_audio_cut_prefixes_the_graph_and_reads_acut(monkeypatch):
    captured = {}

    def fake_run(cmd, **k):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="",
                                           stderr=LOUDNORM_STDERR)
    monkeypatch.setattr(subprocess, "run", fake_run)
    measure_loudness(FIXTURE, start_s=0.0, end_s=5.0, events=[], audio_cut=AUDIO_CUT_GRAPH)
    graphe = captured["cmd"][captured["cmd"].index("-filter_complex") + 1]
    # finding 2 (review Task 7) : [acut] vient d'un concat de plusieurs atrim/asetpts,
    # qui remet les PTS a zero a chaque segment -- combine a -ss/-t en options d'entree
    # (toujours le cas ici) et a loudnorm en aval, ffmpeg lit un DTS non monotone et
    # degrade silencieusement l'audio (tronque ou reordonne, rc=0). aresample AVANT
    # loudnorm absorbe ce reset ; la mesure doit voir le meme graphe que le rendu final.
    assert graphe == (AUDIO_CUT_GRAPH + ";[acut]aresample=48000[acutrs];"
                      "[acutrs]loudnorm=I=-14:TP=-1:LRA=11:"
                      "print_format=json,aresample=48000[aout]")


def test_measure_loudness_places_the_teaser_input_right_after_the_effects(monkeypatch):
    """Meme place que dans la commande de rendu (source, effets, teaser) : le
    graphe audio_cut lit le teaser sur `[1 + nb d'effets:a]` dans les deux."""
    captured = {}

    def fake_run(cmd, **k):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="",
                                           stderr=LOUDNORM_STDERR)
    monkeypatch.setattr(subprocess, "run", fake_run)
    ev = [SfxEvent(0.05, SFX_DIR / "impact.wav", -12.0),
          SfxEvent(1.35, SFX_DIR / "whoosh.wav", -14.0)]
    teaser_input = ["-ss", "41.846", "-t", "1.5", "-i", str(FIXTURE)]
    audio_cut = ("[3:a]asetpts=PTS-STARTPTS[ac0];"
                 "[0:a]atrim=start=0.000:end=4.000,asetpts=PTS-STARTPTS[ac1];"
                 "[ac0][ac1]concat=n=2:v=0:a=1[acut]")
    measure_loudness(FIXTURE, start_s=0.0, end_s=5.0, events=ev, audio_cut=audio_cut,
                     teaser_input=teaser_input)
    cmd = captured["cmd"]
    entrees = [i for i, arg in enumerate(cmd) if arg == "-i"]
    assert [cmd[i + 1] for i in entrees] == [str(FIXTURE), str(SFX_DIR / "impact.wav"),
                                             str(SFX_DIR / "whoosh.wav"), str(FIXTURE)]
    assert cmd[entrees[3] - 4: entrees[3] + 2] == teaser_input
    assert cmd.index("-filter_complex") > entrees[3]


def test_measure_loudness_without_audio_cut_still_reads_0a(monkeypatch):
    captured = {}

    def fake_run(cmd, **k):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="",
                                           stderr=LOUDNORM_STDERR)
    monkeypatch.setattr(subprocess, "run", fake_run)
    measure_loudness(FIXTURE, start_s=0.0, end_s=5.0, events=[])
    graphe = captured["cmd"][captured["cmd"].index("-filter_complex") + 1]
    assert graphe.startswith("[0:a]loudnorm")


def test_mix_graph_src_override_feeds_the_cut_label_instead_of_0a():
    inputs, graphe = audio_filter([], measured=None, src="[acut]")
    assert inputs == []
    assert graphe == ("[acut]aresample=48000[acutrs];"
                      "[acutrs]loudnorm=I=-14:TP=-1:LRA=11,aresample=48000[aout]")


def test_mix_graph_src_override_with_events_feeds_amix_from_the_cut_label():
    ev = [SfxEvent(0.05, SFX_DIR / "impact.wav", -12.0)]
    _, graphe = audio_filter(ev, measured=None, src="[acut]")
    assert graphe.startswith("[acut]aresample=48000[acutrs];[1:a]adelay=50|50")
    assert "[acutrs][sfx1]amix=inputs=2:normalize=0:duration=first[mixed]" in graphe


def test_mix_graph_cut_path_resamples_before_loudnorm_with_no_events():
    """Finding 2 (review Task 7, bisection sur 18 rendus ffmpeg reels) : -ss/-t en
    options d'entree + concat de 2+ segments atrim/asetpts + loudnorm + AAC ->
    "Non-monotonic DTS"/"Queue input is backward in time", puis ffmpeg tronque ou
    reordonne l'audio en SILENCE (rc=0). Un seul evenement SFX masquait le bug par
    le re-timing incident d'amix -- ce test couvre explicitement le chemin SANS
    evenement, celui qui le revele. L'aresample doit venir avant loudnorm sur le
    chemin de coupe (src != "[0:a]")."""
    _, graphe = audio_filter([], measured=None, src="[acut]")
    assert graphe.index("aresample=48000") < graphe.index("loudnorm=I=-14")


def test_mix_graph_cut_path_resamples_before_amix_with_events():
    ev = [SfxEvent(0.05, SFX_DIR / "impact.wav", -12.0)]
    _, graphe = audio_filter(ev, measured=None, src="[acut]")
    assert graphe.index("aresample=48000") < graphe.index("amix=")


def test_mix_graph_v5_path_has_a_single_aresample_after_loudnorm_only():
    """Golden V5 : src par defaut ([0:a], jamais issu d'un concat) ne doit recevoir
    AUCUN aresample avant loudnorm -- seul celui, deja existant, apres loudnorm
    (raison AAC/D2, sans rapport avec ce fix). Cette condition sur src (pas un
    flag explicite) est ce qui garantit le golden V5 intouche."""
    _, graphe = audio_filter([], measured=None)
    assert graphe.count("aresample=48000") == 1
    assert graphe.index("loudnorm=I=-14") < graphe.index("aresample=48000")
