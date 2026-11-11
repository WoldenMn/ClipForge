"""L'orchestrateur de montage, reglages, entrees et replis : reglage
montage ou cut_style invalide, clip sans mots, extraction audio en echec ou
tronquee, VOD sans fps, bornes des mots du clip. Chaque repli se journalise et
n'empeche jamais le rendu.

Decoupe de test_montage_plan.py (revue finale V6, lot D), dont viennent les
scenarios partages. ffmpeg entierement simule (fixture `ffmpeg` de conftest.py).
"""
import json
import re

import pytest
from test_montage_plan import _seed_gap_scenario, _synthetic_energy_wav
from test_render_queue import _clip, _rendre, _seed

from clipforge import db as dbmod
from clipforge.pipeline.audio_extract import AudioExtractError
from clipforge.render import montage_plan as montage_plan_mod
from clipforge.render import sfx as sfx_mod
from clipforge.render.montage_inputs import clip_words


def test_une_vod_sans_fps_previent_que_toute_la_video_passe_a_24_ips(
        tmp_path, ffmpeg, monkeypatch, caplog):
    """Revue A2, fix round 1 (M2) : en V6, la cadence de repli n'est plus celle
    du seul zoompan, toute la video du clip est reechantillonnee dessus."""
    monkeypatch.setattr(montage_plan_mod, "read_wav_mono", _synthetic_energy_wav)
    clip_id = _seed_gap_scenario(tmp_path / "clipforge.db", fps=None)
    with caplog.at_level("WARNING"):
        _rendre(tmp_path, clip_id)
    assert _clip(tmp_path, clip_id)["status"] == "done"
    assert set(re.findall(r"\bfps=([^:,\[;]+)", ffmpeg.filter_complex)) == {"24/1"}
    assert "toute la video" in caplog.text and "24" in caplog.text, caplog.text


def _morceaux_video(fc: str) -> list[tuple[str, str]]:
    return re.findall(r"\[0:v\]trim=start=([\d.]+):end=([\d.]+)", fc)


def test_clip_sans_mots_ne_declenche_aucune_extraction_et_garde_l_edl_plein(
        tmp_path, ffmpeg, monkeypatch):
    """Rien a decouper sans mots : extract_wav ne doit jamais etre appelee (une
    extraction audio reelle ne doit JAMAIS tourner en test unitaire), et l'EDL
    reste plein : un seul morceau video, le clip entier, l'audio tel quel. Le
    punch-in du clip active zoompan, d'ou le passage par la grille (revue A2,
    fix round 1)."""
    def _extraction_interdite(*a, **k):
        pytest.fail("extract_wav ne doit pas etre appelee pour un clip sans mots")
    monkeypatch.setattr(montage_plan_mod, "extract_wav", _extraction_interdite)

    clip_id = _seed(tmp_path / "clipforge.db", words_json="[]", montage="1")
    _rendre(tmp_path, clip_id)

    assert _clip(tmp_path, clip_id)["status"] == "done"
    fc = ffmpeg.filter_complex
    assert "zoompan=" in fc and _morceaux_video(fc) == [("0.000", "10.000")], fc
    assert "[0:a]anull[acut]" in fc and "atrim" not in fc, fc


def test_edl_plein_sans_zoompan_ne_passe_pas_par_la_grille(tmp_path, ffmpeg):
    """Sans coupe ni zoompan (aucun mot, VOD plate), cam et jeu lisent la source
    telle quelle : aucun graphe de coupe, comme avant la revue A2."""
    clip_id = _seed(tmp_path / "clipforge.db", words_json="[]", montage="1", signals=False)
    _rendre(tmp_path, clip_id)

    assert _clip(tmp_path, clip_id)["status"] == "done"
    fc = ffmpeg.filter_complex
    assert "zoompan" not in fc and "[vcut" not in fc and "[acut]" not in fc, fc


def test_extraction_audio_en_echec_degrade_en_edl_plein_sans_bloquer_le_rendu(
        tmp_path, ffmpeg, monkeypatch, caplog):
    """Une extraction audio en echec (ffmpeg absent, source corrompue...) est
    une DECISION de montage -- on renonce aux coupes -- jamais un rendu
    bloque : EDL plein, warning journalise, le rendu aboutit quand meme."""
    def _extraction_en_echec(*a, **k):
        raise AudioExtractError("ffmpeg introuvable (simule)")
    monkeypatch.setattr(montage_plan_mod, "extract_wav", _extraction_en_echec)

    clip_id = _seed(tmp_path / "clipforge.db", montage="1")
    with caplog.at_level("WARNING"):
        _rendre(tmp_path, clip_id)

    clip = _clip(tmp_path, clip_id)
    assert clip["status"] == "done", clip["error"]
    assert "extraction" in caplog.text.lower()
    assert _morceaux_video(ffmpeg.filter_complex) == [("0.000", "10.000")], ffmpeg.filter_complex


@pytest.mark.parametrize("valeur", ["yes", ""])
def test_montage_setting_invalide_degrade_en_v5_avec_un_avertissement(
        tmp_path, ffmpeg, caplog, valeur):
    """Une valeur imprevue du reglage "montage" (faute de frappe d'un futur ecran
    de reglages, valeur "yes"/"true"/"2"...) doit degrader vers le repli V5 SANS
    jamais le faire en silence : ce depot interdit la degradation silencieuse.
    La chaine vide (revue finale V6, lot C, C5) annoncait V5 et rendait en V6."""
    clip_id = _seed(tmp_path / "clipforge.db", montage=valeur)
    with caplog.at_level("WARNING"):
        _rendre(tmp_path, clip_id)

    clip = _clip(tmp_path, clip_id)
    assert clip["status"] == "done", clip["error"]
    assert f"montage={valeur!r}" in caplog.text, caplog.text
    assert "v5" in caplog.text.lower()
    assert "concat=" not in ffmpeg.filter_complex, "un reglage invalide doit rester sur le repli V5"


def test_un_wav_de_clip_tronque_degrade_en_edl_plein_journalise(
        tmp_path, ffmpeg, monkeypatch, caplog):
    """Revue finale V6 (lot C, C2) : ffmpeg sortait rc=0 avec 1 s d'audio pour
    un clip de 12 s (piste audio courte) et le montage decidait ses coupes sur
    cette serie partielle, sans trace. Le vrai extract_wav relit maintenant son
    WAV ; l'except existant journalise et replie sur l'EDL plein."""
    monkeypatch.setattr(montage_plan_mod, "read_wav_mono", _synthetic_energy_wav)
    ffmpeg.duree_wav = 1.0
    clip_id = _seed_gap_scenario(tmp_path / "clipforge.db")
    with caplog.at_level("WARNING"):
        _rendre(tmp_path, clip_id)

    assert _clip(tmp_path, clip_id)["status"] == "done", _clip(tmp_path, clip_id)["error"]
    assert "tronquee" in caplog.text and f"clip {clip_id}" in caplog.text, caplog.text
    assert "concat=" not in ffmpeg.filter_complex, ffmpeg.filter_complex


def test_l_extraction_audio_du_clip_est_bornee_dans_le_temps(tmp_path, ffmpeg, monkeypatch):
    monkeypatch.setattr(montage_plan_mod, "read_wav_mono", _synthetic_energy_wav)
    clip_id = _seed_gap_scenario(tmp_path / "clipforge.db")
    _rendre(tmp_path, clip_id)
    extractions = [kw for cmd, kw in zip(ffmpeg.calls, ffmpeg.kwargs)
                   if cmd[-1].endswith(montage_plan_mod.CLIP_AUDIO_NAME + ".part")]
    assert len(extractions) == 1, extractions
    assert extractions[0]["timeout"] == sfx_mod.CLIP_PASS_TIMEOUT_S, extractions


@pytest.mark.parametrize("style", ["bizarre", ""])
def test_un_cut_style_inconnu_est_journalise_et_coupe_au_rythme_standard(
        tmp_path, ffmpeg, monkeypatch, caplog, style):
    """Revue finale V6 (lot C, C7) : un style inconnu retombait sur "standard"
    sans trace (edl.py, .get), a l'inverse du reglage montage. build_cuts leve
    maintenant ; montage_plan journalise et passe "standard"."""
    appels: list[dict] = []
    reel_build_edl = montage_plan_mod.build_edl

    def _build_edl_espion(*args, **kwargs):
        appels.append(kwargs)
        return reel_build_edl(*args, **kwargs)

    monkeypatch.setattr(montage_plan_mod, "read_wav_mono", _synthetic_energy_wav)
    monkeypatch.setattr(montage_plan_mod, "build_edl", _build_edl_espion)
    db_path = tmp_path / "clipforge.db"
    clip_id = _seed_gap_scenario(db_path)
    conn = dbmod.get_db(db_path)
    try:
        dbmod.set_setting(conn, "cut_style", style)
        conn.commit()
    finally:
        conn.close()
    with caplog.at_level("WARNING"):
        _rendre(tmp_path, clip_id)

    assert _clip(tmp_path, clip_id)["status"] == "done", _clip(tmp_path, clip_id)["error"]
    assert [a["cut_style"] for a in appels] == ["standard"], appels
    assert f"cut_style={style!r}" in caplog.text and "standard" in caplog.text, caplog.text
    assert "concat=" in ffmpeg.filter_complex, "le trou muet doit rester coupe au rythme standard"


def test_un_cut_style_inconnu_ne_previent_pas_sur_le_chemin_v5(
        tmp_path, ffmpeg, caplog):
    """Revue finale V6 (fix round 1, R8) : le warning sortait aussi avec
    montage="0", ou le rythme des coupes ne sert a rien -- une alerte sur un
    reglage sans effet apprend a ignorer les alertes."""
    db_path = tmp_path / "clipforge.db"
    clip_id = _seed_gap_scenario(db_path, montage="0")
    conn = dbmod.get_db(db_path)
    try:
        dbmod.set_setting(conn, "cut_style", "bizarre")
        conn.commit()
    finally:
        conn.close()
    with caplog.at_level("WARNING"):
        _rendre(tmp_path, clip_id)

    assert _clip(tmp_path, clip_id)["status"] == "done", _clip(tmp_path, clip_id)["error"]
    assert "cut_style" not in caplog.text, caplog.text


def test_un_mot_pile_sur_les_bornes_du_clip_est_garde(conn):
    """Revue finale V6 (fix round 1, R8) : la borne de clip_words est inclusive
    des deux cotes, et rien ne le disait. Une mutation `start_s <= w.start` en
    `<` avait traverse le corpus de la decoupe du lot C sans mordre."""
    vod_id = dbmod.create_vod(conn, title="v", source_path="s.mp4")
    conn.executemany(
        "INSERT INTO words (vod_id, idx, start_s, end_s, text, probability) "
        "VALUES (?,?,?,?,?,?)",
        [(vod_id, 0, 4.999, 5.4, "avant", 0.9),
         (vod_id, 1, 5.0, 5.4, "pile_au_debut", 0.9),
         (vod_id, 2, 9.6, 10.0, "pile_a_la_fin", 0.9),
         (vod_id, 3, 9.6, 10.001, "apres", 0.9)])
    cur = conn.execute(
        "INSERT INTO clips (vod_id, start_s, end_s, origin) VALUES (?,?,?,'user')",
        (vod_id, 5.0, 10.0))
    clip = conn.execute("SELECT * FROM clips WHERE id=?", (cur.lastrowid,)).fetchone()

    assert [m.text for m in clip_words(conn, clip)] == ["pile_au_debut", "pile_a_la_fin"]

    edite = dict(clip)
    edite["words_json"] = json.dumps(
        [{"start": 4.999, "end": 5.4, "text": "avant"},
         {"start": 5.0, "end": 5.4, "text": "pile_au_debut"},
         {"start": 9.6, "end": 10.0, "text": "pile_a_la_fin"},
         {"start": 9.6, "end": 10.001, "text": "apres"}])
    assert [m.text for m in clip_words(conn, edite)] == ["pile_au_debut", "pile_a_la_fin"]
