"""Orchestrateur du montage : assemble en un dict les kwargs de build_render_cmd
pour un clip (ecriture de l'ASS incluse), et bascule entre le moteur V6
(coupes EDL, zoom/secousse organiques, tiers d'emphase) et le repli V5 exact.

Le reglage "montage" en base tranche : "1" ou absent active le moteur V6,
"0" reproduit le rendu V5 a l'octet -- golden fige par
tests/test_montage_plan_v5.py AVANT cette bascule (Task 8, brief SDD V6).
"""
import json
import logging
import sqlite3
from pathlib import Path

from clipforge import db as dbmod
from clipforge.models import SubtitleStyle
from clipforge.pipeline.audio_analysis import read_wav_mono
from clipforge.pipeline.audio_extract import AudioExtractError, extract_wav
from clipforge.render.captions_pop import build_pop_ass
from clipforge.render.filtergraph import audio_cut, teaser_input_args, video_cut
from clipforge.render.frame_grid import frame_grid
from clipforge.render.montage.edl import CUT_THRESHOLDS, Edl, Segment, build_edl
from clipforge.render.montage.emphasis import word_tiers
from clipforge.render.montage.energy import fine_energy
from clipforge.render.montage.motion import (
    PunchWindow,
    build_motion,
    detect_punches,
    legacy_zoom_expression,
)
from clipforge.render.montage.timemap import TimeMap
from clipforge.render.montage.words_out import map_words_and_tiers, relative_words
from clipforge.render.montage_inputs import (
    audio_series,
    clip_words,
    emoji_overlay,
    source_fps,
)
from clipforge.render.sfx import (
    CLIP_PASS_TIMEOUT_S,
    audio_filter,
    measure_loudness,
    sfx_plan,
)

logger = logging.getLogger(__name__)

ASS_NAME = "captions.ass"
# Garde volontairement tant que le clip existe (les oracles d'integration le
# relisent) ; job_cache.purge_stale_job_caches le retire avec le clip supprime.
CLIP_AUDIO_NAME = "clip_audio.wav"
GRAPH_NAME = "filter_complex.txt"
SHAKE_LEAD_S = 0.2  # avance du declic de secousse dans sa fenetre de punch (brief Task 8)


def _map_punch_windows(tmap: TimeMap, punches: list[PunchWindow]) -> list[PunchWindow]:
    """Fenetres de punch-in projetees sur la timeline de sortie.

    Une fenetre entierement dans une coupe disparait (map_window rend None).
    EDL plein (repli V5, aucune coupe) : projection identite, punches inchangees.
    """
    sorties = []
    for w in punches:
        mappe = tmap.map_window(w.start, w.end)
        if mappe is not None:
            sorties.append(PunchWindow(*mappe))
    return sorties


def build_montage_options(conn: sqlite3.Connection, clip: sqlite3.Row, vod: sqlite3.Row,
                          job_dir: Path) -> dict:
    """Ecrit l'ASS du clip et rend les kwargs de montage de build_render_cmd, plus
    "output_duration_s" (duree de sortie planifiee, que render_clip_job retire
    avant build_render_cmd pour controler le fichier rendu).

    Bascule sur le reglage "montage" (absent ou "1" -> moteur V6 ; "0" ->
    repli V5, rendu identique a l'octet). "cut_style" inconnu retombe sur
    "standard" avec un warning journalise (build_cuts, lui, leve). Une
    extraction audio en echec (AudioExtractError) degrade en EDL plein avec
    un warning journalise -- decision de montage, jamais un rendu bloque ;
    une erreur de calcul pur, elle, n'est pas rattrapee ici et fait echouer
    le rendu (comportement par defaut de render_clip_job).
    """
    dur_s = round(clip["end_s"] - clip["start_s"], 3)
    style = SubtitleStyle(**json.loads(clip["subtitle_style"] or "{}"))
    hook = (clip["hook"] or "").strip() or None
    handle = (dbmod.get_setting(conn, "channel_handle") or "").strip() or None
    fps = source_fps(vod)

    montage_reglage = dbmod.get_setting(conn, "montage")
    if montage_reglage not in (None, "0", "1"):
        logger.warning(
            "Reglage montage=%r invalide (ni absent, ni '0', ni '1') : rendu V5 "
            "utilise pour le clip %s", montage_reglage, clip["id"])
    montage_actif = montage_reglage in (None, "1")
    cut_style = dbmod.get_setting(conn, "cut_style")
    if montage_actif and cut_style not in (None, *CUT_THRESHOLDS):
        logger.warning(
            "Reglage cut_style=%r inconnu (ni absent, ni %s) : rythme 'standard' "
            "utilise pour le clip %s", cut_style, "/".join(CUT_THRESHOLDS), clip["id"])
    cut_style = cut_style if cut_style in CUT_THRESHOLDS else "standard"

    mots_abs = clip_words(conn, clip)
    mots_rel = relative_words(mots_abs, clip["start_s"])

    energy: list[tuple[float, float]] = []
    edl = None
    if montage_actif and mots_rel:
        try:
            wav = extract_wav(Path(vod["source_path"]), job_dir / CLIP_AUDIO_NAME,
                              start_s=clip["start_s"], end_s=clip["end_s"],
                              timeout_s=CLIP_PASS_TIMEOUT_S)
            samples, sr = read_wav_mono(wav)
            energy = fine_energy(samples, sr)
        except AudioExtractError as exc:
            logger.warning(
                "Extraction audio du clip %s impossible (%s) : montage renonce, "
                "EDL plein pour ce rendu", clip["id"], exc)
            edl = Edl(None, [Segment(0.0, dur_s)])
    if edl is None:
        edl = (build_edl(mots_rel, energy, clip_dur_s=dur_s, cut_style=cut_style)
              if montage_actif else Edl(None, [Segment(0.0, dur_s)]))

    tmap = TimeMap.from_edl(edl)

    punches = detect_punches(audio_series(conn, clip["vod_id"]),
                             clip_start=clip["start_s"], clip_end=clip["end_s"])
    fenetres_sortie = _map_punch_windows(tmap, punches)
    shake_at = [w.start + SHAKE_LEAD_S for w in fenetres_sortie]

    if montage_actif:
        tiers = word_tiers(mots_rel, energy)
        ass_words, ass_tiers = map_words_and_tiers(tmap, mots_rel, tiers)
        ass_cuts = tmap.cut_times()
        motion = build_motion(fenetres_sortie, ass_cuts, shake_at=shake_at, fps=fps)
        zoom_expr, x_expr, y_expr = motion.zoom_expr, motion.x_expr, motion.y_expr
        ass_start_s, ass_dur_s = 0.0, tmap.output_duration
    else:
        zoom_expr, x_expr, y_expr = legacy_zoom_expression(punches), None, None
        ass_words, ass_tiers, ass_cuts = mots_abs, None, None
        # Duree brute comme le V5 : arrondie, la signature bougeait d'un centieme
        ass_start_s, ass_dur_s = clip["start_s"], clip["end_s"] - clip["start_s"]

    (job_dir / ASS_NAME).write_text(
        build_pop_ass(ass_words, style, clip_start_s=ass_start_s, clip_dur_s=ass_dur_s,
                      hook=hook, handle=handle, tiers=ass_tiers, cut_times=ass_cuts),
        encoding="utf-8")

    source = Path(vod["source_path"])
    teaser_cut_s = tmap.cut_times()[0] if edl.teaser else None
    events = sfx_plan(fenetres_sortie, with_hook=hook is not None, teaser_cut_s=teaser_cut_s)
    # Mesure (source, effets, teaser) comme rendu (source, effets, teaser, emoji) : un
    # -i par effet, l'entree teaser juste derriere, donc a l'index 1 + len(events) dans les deux.
    teaser_index = 1 + len(events) if edl.teaser else None
    teaser_input = (teaser_input_args(source, start_s=clip["start_s"], teaser=edl.teaser)
                    if edl.teaser else [])
    zoompan = montage_actif and zoom_expr != "1"
    video_cut_graph = video_cut(edl, clip_dur_s=dur_s, fps=fps, teaser_index=teaser_index,
                                zoompan=zoompan)
    audio_cut_graph = audio_cut(edl, clip_dur_s=dur_s, teaser_index=teaser_index,
                                zoompan=zoompan)
    measured = measure_loudness(source, start_s=clip["start_s"], end_s=clip["end_s"],
                                events=events, audio_cut=audio_cut_graph,
                                teaser_input=teaser_input)
    sfx_inputs, audio_graph = audio_filter(
        events, measured=measured, src="[acut]" if audio_cut_graph else "[0:a]")

    return {
        "fps": frame_grid(fps) if montage_actif else fps,
        "zoom_expr": zoom_expr, "x_expr": x_expr, "y_expr": y_expr,
        "emoji": emoji_overlay(conn, clip, tmap.output_duration),
        "sfx_inputs": sfx_inputs, "audio_graph": audio_graph,
        "video_cut": video_cut_graph, "audio_cut": audio_cut_graph,
        "teaser_input": teaser_input,
        "graph_file": job_dir / GRAPH_NAME if montage_actif else None,
        "output_duration_s": tmap.output_duration,
    }
