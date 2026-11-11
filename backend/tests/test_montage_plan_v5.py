"""L'orchestrateur de montage, repli V5 (reglage montage=0) : le
golden fige avant la bascule de queue.py vers montage_plan.py, et la promesse
qu'il tient a l'octet.

Decoupe de test_montage_plan.py (revue finale V6, lot D). ffmpeg entierement
simule (fixture `ffmpeg` de conftest.py).
"""
import json

import pytest
from test_render_queue import _ass, _clip, _rendre, _seed

from clipforge import db as dbmod
from clipforge.models import SubtitleStyle
from clipforge.render import montage_plan as montage_plan_mod
from clipforge.render.captions_pop import build_pop_ass
from clipforge.render.montage.motion import (
    detect_punches,
    legacy_zoom_expression,
)
from clipforge.render.montage_inputs import audio_series, clip_words
from clipforge.render.sfx import audio_filter, emoji_path, sfx_plan


def test_montage_off_matches_the_v5_dict_and_ass_exactly(tmp_path, ffmpeg):
    """montage=0 doit rendre EXACTEMENT ce que l'ancien _montage rendait (golden
    fige avant la bascule de queue.py vers montage_plan.py, cf. rapport Task 8).

    L'attendu est reconstruit avec les MEMES primitives que l'ancien code
    (build_pop_ass, legacy_zoom_expression, sfx_plan, audio_filter), toutes
    inchangees par cette tache : le contrat verifie est celui du brief (mots
    absolus, clip_start_s=clip["start_s"], pas de tiers), pas une recopie
    figee et fragile d'une chaine.
    """
    db_path = tmp_path / "clipforge.db"
    clip_id = _seed(db_path, montage="0")
    conn = dbmod.get_db(db_path)
    try:
        clip = conn.execute("SELECT * FROM clips WHERE id=?", (clip_id,)).fetchone()
        vod = dbmod.get_vod(conn, clip["vod_id"])
        job_dir = tmp_path / "jobs" / f"clip{clip_id}"
        job_dir.mkdir(parents=True)
        options = montage_plan_mod.build_montage_options(conn, clip, vod, job_dir)

        dur_s = clip["end_s"] - clip["start_s"]
        style = SubtitleStyle(**json.loads(clip["subtitle_style"] or "{}"))
        hook = (clip["hook"] or "").strip() or None
        handle = (dbmod.get_setting(conn, "channel_handle") or "").strip() or None
        mots = clip_words(conn, clip)
        ass_attendu = build_pop_ass(mots, style, clip_start_s=clip["start_s"],
                                    clip_dur_s=dur_s, hook=hook, handle=handle)
        punches = detect_punches(audio_series(conn, clip["vod_id"]),
                                 clip_start=clip["start_s"], clip_end=clip["end_s"])
        events = sfx_plan(punches, with_hook=hook is not None)
        sfx_inputs_attendus, audio_graph_attendu = audio_filter(events, measured=None)

        assert (job_dir / montage_plan_mod.ASS_NAME).read_text(encoding="utf-8") == ass_attendu
        assert options["fps"] == 30.0
        assert options["zoom_expr"] == legacy_zoom_expression(punches)
        assert options["x_expr"] is None and options["y_expr"] is None
        assert options["video_cut"] == "" and options["audio_cut"] == ""
        assert options["emoji"] == (emoji_path("💀"), 0.0, 2.8)
        assert options["sfx_inputs"] == sfx_inputs_attendus
        assert options["audio_graph"] == audio_graph_attendu
        assert options["output_duration_s"] == 10.0
    finally:
        conn.close()


def test_montage_off_renders_the_full_pipeline_byte_identical_to_v5(tmp_path, ffmpeg):
    """Meme golden que ci-dessus, au niveau du rendu complet (render_clip_job) :
    ASS ecrit et filter_complex assembles, chaines figees sur une capture
    manuelle du VRAI _montage V5, faite AVANT toute modification de queue.py
    (cf. rapport Task 8, methode de capture)."""
    clip_id = _seed(tmp_path / "clipforge.db", montage="0")
    _rendre(tmp_path, clip_id)

    assert _ass(tmp_path, clip_id) == GOLDEN_ASS_V5
    assert ffmpeg.filter_complex == GOLDEN_FILTER_COMPLEX_V5
    cmd = ffmpeg.calls[-1]
    assert cmd[cmd.index("-t") + 1] == "10.0"
    assert "-/filter_complex" not in cmd
    assert _clip(tmp_path, clip_id)["status"] == "done"


GOLDEN_ASS_V5 = (
    "[Script Info]\n"
    "ScriptType: v4.00+\n"
    "PlayResX: 1080\n"
    "PlayResY: 1920\n"
    "WrapStyle: 0\n"
    "ScaledBorderAndShadow: yes\n"
    "\n"
    "[V4+ Styles]\n"
    "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
    "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
    "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
    "Style: Pop,Segoe UI,88,&H00FFFFFF,&H0000D7FF,&H00000000,&H80000000,-1,0,0,0,100,100,"
    "0,0,1,4,3,5,90,90,0,1\n"
    "Style: HookRim,Segoe UI,64,&HFF000000,&HFF000000,&H0000D7FF,&HB0080808,-1,0,0,0,100,100,"
    "0,0,3,5,0,8,220,220,0,1\n"
    "Style: Hook,Segoe UI,64,&H00FFFFFF,&H0000D7FF,&H00000000,&HB0080808,-1,0,0,0,100,100,"
    "0,0,3,3,0,8,220,220,0,1\n"
    "Style: Sign,Segoe UI,34,&H60FFFFFF,&H0000D7FF,&H60000000,&H00000000,-1,0,0,0,100,100,"
    "3,0,1,1,0,5,90,90,0,1\n"
    "\n"
    "[Events]\n"
    "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    "Dialogue: 0,0:00:01.00,0:00:02.40,Pop,,0,0,0,,{\\pos(540,1180)\\fscx30\\fscy30"
    "\\t(0,70,\\fscx112\\fscy112)\\t(70,130,\\fscx100\\fscy100)}IL {\\1c&H0000D7FF}MANQUE"
    "{\\1c&H00FFFFFF} DE\n"
    "Dialogue: 0,0:00:02.50,0:00:02.90,Pop,,0,0,0,,{\\pos(540,1180)\\fscx30\\fscy30"
    "\\t(0,70,\\fscx112\\fscy112)\\t(70,130,\\fscx100\\fscy100)}{\\1c&H0000D7FF}MOURIR"
    "{\\1c&H00FFFFFF}\n"
    "Dialogue: 1,0:00:00.00,0:00:02.80,HookRim,,0,0,0,,{\\pos(540,250)\\fad(120,180)"
    "\\fscx30\\fscy30\\t(0,70,\\fscx112\\fscy112)\\t(70,130,\\fscx100\\fscy100)}"
    "IL MANQUE DE MOURIR\n"
    "Dialogue: 2,0:00:00.00,0:00:02.80,Hook,,0,0,0,,{\\pos(540,250)\\fad(120,180)"
    "\\fscx30\\fscy30\\t(0,70,\\fscx112\\fscy112)\\t(70,130,\\fscx100\\fscy100)}"
    "IL MANQUE DE MOURIR\n"
    "Dialogue: 0,0:00:00.00,0:00:10.00,Sign,,0,0,0,,{\\pos(540,606)}TWITCH.TV/PSEUDO\n"
)

GOLDEN_FILTER_COMPLEX_V5 = (
    "[0:v]crop=640:340:0:0,scale=1080:576:flags=lanczos,"
    "zoompan=z='1+0.22*(between(it,2.800,4.200))':d=1:x='iw/2-(iw/zoom/2)':"
    "y='ih*0.38-(ih/zoom*0.38)':s=1080x576:fps=30.0[top];"
    "[0:v]crop=868:1080:640:0,scale=1080:1344:flags=lanczos[bot];"
    "[top][bot]vstack=inputs=2[stacked];"
    "[stacked]subtitles=captions.ass[v0];"
    "[3:v]scale=160:-1[emo];"
    "[v0][emo]overlay=x=main_w-200:y=180:shortest=1:enable='between(t,0.000,2.800)'[v];"
    "[1:a]adelay=50|50,aformat=channel_layouts=stereo,volume=-12.0dB[sfx1];"
    "[2:a]adelay=2650|2650,aformat=channel_layouts=stereo,volume=-14.0dB[sfx2];"
    "[0:a][sfx1][sfx2]amix=inputs=3:normalize=0:duration=first[mixed];"
    "[mixed]loudnorm=I=-14:TP=-1:LRA=11,aresample=48000[aout]"
)


@pytest.mark.parametrize("bornes, signature", [((0.001, 16.136), "0:00:16.13"),
                                              ((36.3053, 68.7799), "0:00:32.47")])
def test_montage_off_garde_la_duree_brute_du_v5_dans_l_ass(tmp_path, ffmpeg, bornes, signature):
    """Revue finale V6 (lot C, C6) : la branche V5 passait a l'ASS la duree
    arrondie a la ms, le V5 (25c33ef, render/queue.py:_montage) la duree brute
    clip["end_s"] - clip["start_s"]. Sur (36.3053, 68.7799) la signature
    finissait a 32.48 au lieu de 32.47 ; sur (0.001, 16.136), une image
    differait au rendu NVENC. Oracle : l'appel de build_pop_ass du V5."""
    db_path = tmp_path / "clipforge.db"
    clip_id = _seed(db_path, montage="0")
    conn = dbmod.get_db(db_path)
    try:
        conn.execute("UPDATE clips SET start_s=?, end_s=? WHERE id=?", (*bornes, clip_id))
        conn.commit()
        clip = conn.execute("SELECT * FROM clips WHERE id=?", (clip_id,)).fetchone()
        job_dir = tmp_path / "jobs" / f"clip{clip_id}"
        job_dir.mkdir(parents=True)
        montage_plan_mod.build_montage_options(
            conn, clip, dbmod.get_vod(conn, clip["vod_id"]), job_dir)

        dur_s = clip["end_s"] - clip["start_s"]
        style = SubtitleStyle(**json.loads(clip["subtitle_style"] or "{}"))
        hook = (clip["hook"] or "").strip() or None
        handle = (dbmod.get_setting(conn, "channel_handle") or "").strip() or None
        attendu = build_pop_ass(clip_words(conn, clip), style, clip_start_s=clip["start_s"],
                                clip_dur_s=dur_s, hook=hook, handle=handle)
    finally:
        conn.close()
    ass = (job_dir / montage_plan_mod.ASS_NAME).read_text(encoding="utf-8")
    assert f"0:00:00.00,{signature},Sign," in attendu, attendu
    assert ass == attendu
