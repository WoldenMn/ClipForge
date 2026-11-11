"""Le mode automatique : fabriquer, ecrire, monter — et ce qu'il ne fait jamais.

Les trois etapes tournent ici sans reseau ni ffmpeg : write_clips et
render_clip_job sont remplaces. Ce qui est verifie, c'est le CABLAGE (qui est
appele, avec quoi, ce qui atterrit en base), pas ce que font ces deux-la, deja
couvert par test_ai_writing.py et test_render_queue.py.
"""
import re
from pathlib import Path

import pytest

from clipforge import config
from clipforge import db as dbmod
from clipforge.pipeline import autoclip
from clipforge.pipeline.runner import run_steps

PROFILE_SQL = (
    "INSERT INTO scene_profiles (name, cam_x, cam_y, cam_w, cam_h, "
    "game_x, game_y, game_w, game_h) VALUES ('scene',0,0,640,340,640,0,868,1080)")

# Capture AVANT que la fixture ci-dessous ne le remplace : un test a besoin du vrai.
SETUP_LOGGING_REEL = config.setup_logging


@pytest.fixture(autouse=True)
def journal_neutralise(monkeypatch):
    """Neutralise le journal : le montage le rouvre apres chaque clip, dans data/."""
    monkeypatch.setattr(config, "setup_logging", lambda: None)


def _seed(conn, *, segments: int = 8, profil: bool = True, count: str = "5") -> int:
    """Une VOD decoupee en `segments` candidats classes, un profil de scene, N clips voulus."""
    vod_id = dbmod.create_vod(conn, title="vod", source_path="x.mp4")
    for rang in range(1, segments + 1):
        conn.execute(
            "INSERT INTO segments (vod_id, start_s, end_s, score, rank, hook_text) "
            "VALUES (?,?,?,?,?,?)",
            (vod_id, rang * 100.0, rang * 100.0 + 30.0, 1.0 - rang * 0.05, rang,
             f"accroche du candidat {rang}"))
    if profil:
        conn.execute(PROFILE_SQL)
    conn.commit()
    dbmod.set_setting(conn, "auto_clip_count", count)
    return vod_id


def _clip_ids(conn, vod_id: int) -> list[int]:
    return [r["id"] for r in conn.execute(
        "SELECT id FROM clips WHERE vod_id=? ORDER BY id", (vod_id,))]


def test_autoclips_creates_top_n_marked_auto_with_profile(conn):
    vod_id = _seed(conn)
    autoclip.step_autoclips(conn, vod_id, {})
    rows = conn.execute(
        "SELECT clips.origin, clips.profile_id, clips.segment_id, segments.rank AS rang "
        "FROM clips JOIN segments ON segments.id = clips.segment_id "
        "ORDER BY clips.id").fetchall()
    assert len(rows) == 5
    assert all(r["origin"] == "auto" for r in rows)
    assert all(r["profile_id"] is not None for r in rows)
    assert [r["rang"] for r in rows] == [1, 2, 3, 4, 5]


def test_autoclips_take_the_profile_of_the_vod_over_the_setting(conn):
    """Pont OBS : les clips d'un enregistrement gardent son cadrage, pas celui du reglage."""
    vod_id = _seed(conn)
    obs = conn.execute(
        "INSERT INTO scene_profiles (name, cam_x, cam_y, cam_w, cam_h, game_x, game_y, "
        "game_w, game_h) VALUES ('obs',1874,1042,636,340,701,0,1158,1440)").lastrowid
    conn.execute("UPDATE vods SET profile_id=? WHERE id=?", (obs, vod_id))
    conn.commit()
    dbmod.set_setting(conn, "auto_profile", "scene")
    autoclip.step_autoclips(conn, vod_id, {})
    assert {r["profile_id"] for r in conn.execute("SELECT profile_id FROM clips")} == {obs}


def test_autoclips_stamps_the_default_subtitle_style(conn):
    """Meme regle que create_clip : le prereglage est fige a la creation."""
    vod_id = _seed(conn)
    dbmod.set_setting(conn, "subtitle_style_default", '{"font_size": 96}')
    autoclip.step_autoclips(conn, vod_id, {})
    styles = {r["subtitle_style"] for r in conn.execute("SELECT subtitle_style FROM clips")}
    assert styles == {'{"font_size": 96}'}


def test_autoclips_is_idempotent_on_resume(conn):
    vod_id = _seed(conn)
    autoclip.step_autoclips(conn, vod_id, {})
    autoclip.step_autoclips(conn, vod_id, {})
    assert conn.execute("SELECT COUNT(*) FROM clips").fetchone()[0] == 5


def test_autoclips_never_removes_the_drafts_of_a_previous_analysis(conn):
    """Une re-analyse remplace les candidats : les clips deja affiches restent.

    La machine ne fait jamais disparaitre un clip visible par l'utilisateur
    (principe V3) ; elle fabrique seulement ceux des nouveaux candidats. Les
    anciens perdent leur segment_id par la cascade et restent dans la grille.
    """
    vod_id = _seed(conn)
    autoclip.step_autoclips(conn, vod_id, {})
    conn.execute("DELETE FROM segments WHERE vod_id=?", (vod_id,))
    for rang in range(1, 6):
        conn.execute(
            "INSERT INTO segments (vod_id, start_s, end_s, score, rank) VALUES (?,?,?,?,?)",
            (vod_id, rang * 50.0, rang * 50.0 + 20.0, 0.5, rang))
    conn.commit()
    autoclip.step_autoclips(conn, vod_id, {})
    assert conn.execute("SELECT COUNT(*) FROM clips").fetchone()[0] == 10
    assert conn.execute(
        "SELECT COUNT(*) FROM clips WHERE segment_id IS NULL").fetchone()[0] == 5


def test_autoclips_makes_fewer_clips_than_asked_when_candidates_run_out(conn):
    vod_id = _seed(conn, segments=3)
    autoclip.step_autoclips(conn, vod_id, {})
    assert conn.execute("SELECT COUNT(*) FROM clips").fetchone()[0] == 3


def test_a_count_of_zero_leaves_the_three_steps_without_anything_to_do(conn, monkeypatch):
    vod_id = _seed(conn, count="0")
    monkeypatch.setattr(autoclip, "write_clips", lambda *a: pytest.fail("appel inattendu"))
    monkeypatch.setattr(autoclip, "render_clip_job", lambda *a: pytest.fail("appel inattendu"))
    autoclip.step_autoclips(conn, vod_id, {})
    autoclip.step_writing(conn, vod_id, {})
    autoclip.step_autorender(conn, vod_id, {})
    assert conn.execute("SELECT COUNT(*) FROM clips").fetchone()[0] == 0


def test_an_unreadable_count_falls_back_on_the_default_and_says_so(conn, caplog):
    """Un reglage corrompu ne doit pas couper le mode auto en silence."""
    vod_id = _seed(conn, count="beaucoup")
    with caplog.at_level("WARNING"):
        autoclip.step_autoclips(conn, vod_id, {})
    assert conn.execute("SELECT COUNT(*) FROM clips").fetchone()[0] == 5
    assert "auto_clip_count" in caplog.text


def test_auto_steps_write_no_usage_events(conn, monkeypatch):
    """Un evenement compte un GESTE de l'utilisateur, jamais un appel de la machine."""
    vod_id = _seed(conn)
    monkeypatch.setattr(autoclip, "write_clips", lambda c, v, ids: True)
    monkeypatch.setattr(autoclip, "render_clip_job", lambda *a: None)
    autoclip.step_autoclips(conn, vod_id, {})
    autoclip.step_writing(conn, vod_id, {})
    autoclip.step_autorender(conn, vod_id, {})
    assert conn.execute("SELECT COUNT(*) FROM usage_events").fetchone()[0] == 0


def test_writing_only_asks_for_the_auto_clips_without_a_hook(conn, monkeypatch):
    """write_clips ecrase ce qu'on lui donne : le filtre est ce qui rend la
    reprise gratuite ET ce qui protege une accroche deja ecrite."""
    vod_id = _seed(conn)
    autoclip.step_autoclips(conn, vod_id, {})
    ids = _clip_ids(conn, vod_id)
    conn.execute("UPDATE clips SET hook='DEJA ECRITE' WHERE id IN (?,?)", (ids[0], ids[1]))
    conn.execute("INSERT INTO clips (vod_id, start_s, end_s, origin) VALUES (?,?,?,'user')",
                 (vod_id, 1.0, 2.0))
    conn.commit()
    demandes = []
    monkeypatch.setattr(autoclip, "write_clips",
                        lambda c, v, clip_ids: demandes.append(list(clip_ids)) or True)
    autoclip.step_writing(conn, vod_id, {})
    assert demandes == [ids[2:]]


def test_writing_is_a_noop_when_every_hook_is_already_there(conn, monkeypatch):
    vod_id = _seed(conn)
    autoclip.step_autoclips(conn, vod_id, {})
    conn.execute("UPDATE clips SET hook='DEJA' WHERE vod_id=?", (vod_id,))
    conn.commit()
    monkeypatch.setattr(autoclip, "write_clips", lambda *a: pytest.fail("appel inattendu"))
    autoclip.step_writing(conn, vod_id, {})


def test_writing_says_it_when_the_local_fallback_took_over(conn, monkeypatch, caplog):
    vod_id = _seed(conn)
    autoclip.step_autoclips(conn, vod_id, {})
    monkeypatch.setattr(autoclip, "write_clips", lambda *a: False)
    with caplog.at_level("WARNING"):
        autoclip.step_writing(conn, vod_id, {})
    assert "repli" in caplog.text.lower()


def test_one_failed_render_does_not_stop_the_others(conn, monkeypatch):
    vod_id = _seed(conn)
    autoclip.step_autoclips(conn, vod_id, {})
    ids = _clip_ids(conn, vod_id)
    deuxieme = ids[1]
    rendus = []

    def faux_job(clip_id, *a):
        if clip_id == deuxieme:
            raise RuntimeError("boom")
        rendus.append(clip_id)

    monkeypatch.setattr(autoclip, "render_clip_job", faux_job)
    autoclip.step_autorender(conn, vod_id, {})
    assert rendus == [i for i in ids if i != deuxieme]
    ligne = conn.execute("SELECT status, error FROM clips WHERE id=?", (deuxieme,)).fetchone()
    assert ligne["status"] == "error" and "boom" in ligne["error"]


def test_the_vod_ends_ready_even_when_a_clip_failed(conn, monkeypatch):
    """L'erreur du clip est visible sur SA carte ; elle ne condamne pas la VOD."""
    vod_id = _seed(conn)
    autoclip.step_autoclips(conn, vod_id, {})
    rate = _clip_ids(conn, vod_id)[0]

    def faux_job(clip_id, *a):
        if clip_id == rate:
            raise RuntimeError("boom")

    monkeypatch.setattr(autoclip, "render_clip_job", faux_job)
    run_steps(conn, vod_id, [("rendering_clips", autoclip.step_autorender)], {})
    assert dbmod.get_vod(conn, vod_id)["status"] == "ready"
    assert conn.execute("SELECT error FROM clips WHERE id=?", (rate,)).fetchone()["error"]


def test_autorender_reports_the_vod_progress_clip_by_clip(conn, monkeypatch):
    vod_id = _seed(conn)
    autoclip.step_autoclips(conn, vod_id, {})
    dbmod.set_vod_status(conn, vod_id, "rendering_clips")
    vues = []
    monkeypatch.setattr(autoclip, "render_clip_job",
                        lambda clip_id, *a: vues.append(dbmod.get_vod(conn, vod_id)["progress"]))
    autoclip.step_autorender(conn, vod_id, {})
    assert vues == [0.0, 20.0, 40.0, 60.0, 80.0]
    assert dbmod.get_vod(conn, vod_id)["progress"] == 100.0


def test_autorender_eta_after_the_first_clip_counts_its_full_duration(conn, monkeypatch):
    """L'horloge de l'ETA doit demarrer AVANT le premier clip, pas a son premier
    appel reel (qui n'arrive qu'apres le premier montage) : sinon la duree du
    clip 1 n'entre jamais dans l'extrapolation et l'ETA affichee est deux fois
    trop optimiste (bug V5). Trois clips de 10 s chacun, horloge factice comme
    dans test_progress.py : apres le clip 1 (1/3 fait, 10 s ecoulees), il reste
    deux clips de 10 s, donc ~20 s — pas ~10.
    """
    from clipforge.pipeline import runner as runner_mod

    vod_id = _seed(conn, count="3")
    autoclip.step_autoclips(conn, vod_id, {})
    dbmod.set_vod_status(conn, vod_id, "rendering_clips")

    horloge = {"t": 1000.0}
    monkeypatch.setattr(
        autoclip, "build_progress_cb",
        lambda c, v, s: runner_mod.build_progress_cb(c, v, s, clock=lambda: horloge["t"]))

    etas_avant_chaque_clip = []

    def faux_job(clip_id, *a):
        etas_avant_chaque_clip.append(dbmod.get_vod(conn, vod_id)["eta_s"])
        horloge["t"] += 10.0  # chaque montage "prend" 10 s

    monkeypatch.setattr(autoclip, "render_clip_job", faux_job)
    autoclip.step_autorender(conn, vod_id, {})

    # etas_avant_chaque_clip[1] : ETA lue juste avant le clip 2, donc telle
    # qu'ecrite juste apres la fin du clip 1.
    assert etas_avant_chaque_clip[1] == pytest.approx(20.0)


def test_autorender_skips_the_clips_already_mounted(conn, monkeypatch):
    """Reprise apres coupure : remonter quatre clips deja faits couterait
    plusieurs minutes pour rien. Seul celui que le redemarrage a bascule en
    erreur repart."""
    vod_id = _seed(conn)
    autoclip.step_autoclips(conn, vod_id, {})
    ids = _clip_ids(conn, vod_id)
    conn.executemany("UPDATE clips SET status='done' WHERE id=?", [(i,) for i in ids[:4]])
    conn.execute("UPDATE clips SET status='error', error='interrompu par redemarrage' "
                 "WHERE id=?", (ids[4],))
    conn.commit()
    rendus = []
    monkeypatch.setattr(autoclip, "render_clip_job", lambda clip_id, *a: rendus.append(clip_id))
    autoclip.step_autorender(conn, vod_id, {})
    assert rendus == [ids[4]]


def test_autorender_leaves_the_clips_the_pool_is_already_rendering(conn, monkeypatch):
    """Fix round 3 (F1) : « Tout relancer » met les clips en file du pool ; une
    reprise de l'analyse les rendait une seconde fois, dans son propre process.
    Mesure du relecteur (vraie app, vrai pool) : clip livre deux fois 5 fois sur
    5, et un fichier corrompu marque 'done' une fois sur trois."""
    vod_id = _seed(conn)
    autoclip.step_autoclips(conn, vod_id, {})
    ids = _clip_ids(conn, vod_id)
    for clip_id, statut in zip(ids, ("queued", "rendering", "error", "done", "draft")):
        conn.execute("UPDATE clips SET status=? WHERE id=?", (statut, clip_id))
    conn.commit()
    rendus = []
    monkeypatch.setattr(autoclip, "render_clip_job", lambda clip_id, *a: rendus.append(clip_id))
    autoclip.step_autorender(conn, vod_id, {})
    assert rendus == [ids[2], ids[4]]


def test_autorender_leaves_the_user_clips_alone(conn, monkeypatch):
    vod_id = _seed(conn)
    autoclip.step_autoclips(conn, vod_id, {})
    conn.execute("INSERT INTO clips (vod_id, start_s, end_s, origin) VALUES (?,?,?,'user')",
                 (vod_id, 1.0, 2.0))
    conn.commit()
    a_la_main = _clip_ids(conn, vod_id)[-1]
    rendus = []
    monkeypatch.setattr(autoclip, "render_clip_job", lambda clip_id, *a: rendus.append(clip_id))
    autoclip.step_autorender(conn, vod_id, {})
    assert a_la_main not in rendus


def test_the_render_is_pointed_at_the_database_the_step_writes(conn, tmp_path, monkeypatch):
    """render_clip_job rouvre la base par son CHEMIN : viser celle de la
    configuration plutot que celle de l'etape monterait les clips d'une autre
    base (suite de tests, CLIPFORGE_DATA deplace entre deux lancements)."""
    vod_id = _seed(conn)
    autoclip.step_autoclips(conn, vod_id, {})
    vus = []
    monkeypatch.setattr(autoclip, "render_clip_job",
                        lambda clip_id, db_path, jobs, out: vus.append(db_path))
    autoclip.step_autorender(conn, vod_id, {})
    assert {Path(v).resolve() for v in vus} == {(tmp_path / "test.db").resolve()}


def test_the_real_pipeline_tail_turns_candidates_into_mounted_clips(conn, monkeypatch):
    """Le bout du pipeline REEL, exigences comprises : candidats -> clips montes.

    Les trois etapes sont prises dans build_real_steps et non appelees a la main :
    c'est le cablage (noms, ordre, exigences declarees sur les bons noms) qui est
    verifie ici, pas leur contenu.
    """
    from clipforge.pipeline.preconditions import build_preconditions
    from clipforge.pipeline.steps import build_real_steps

    vod_id = _seed(conn)
    monkeypatch.setattr(autoclip, "write_clips", lambda c, v, ids: True)
    montes = []
    monkeypatch.setattr(autoclip, "render_clip_job", lambda clip_id, *a: montes.append(clip_id))
    run_steps(conn, vod_id, build_real_steps(), {}, resume_from="making_clips",
              preconditions=build_preconditions())
    vod = dbmod.get_vod(conn, vod_id)
    assert vod["status"] == "ready" and vod["progress"] == 100.0
    assert montes == _clip_ids(conn, vod_id)
    assert len(montes) == 5


def test_a_vod_without_a_single_candidate_still_ends_ready(conn):
    """Zero candidat n'est pas une panne : c'est un stream sans moment fort.

    Le refuser enfermait la VOD dans une erreur SANS RETOUR : la reprise
    repointe sur making_clips, le decoupage ne rejoue jamais, et la
    transcription est perdue — le cout exact que la V4 avait supprime. Avant la
    V5 cette VOD finissait 'ready' avec une grille vide ; elle doit continuer.
    """
    from clipforge.pipeline.preconditions import build_preconditions
    from clipforge.pipeline.steps import build_real_steps

    vod_id = _seed(conn, segments=0, profil=False)
    run_steps(conn, vod_id, build_real_steps(), {}, resume_from="making_clips",
              preconditions=build_preconditions())
    assert dbmod.get_vod(conn, vod_id)["status"] == "ready"
    assert conn.execute("SELECT COUNT(*) FROM clips").fetchone()[0] == 0


def test_a_resume_after_the_user_deleted_the_auto_clips_still_ends_ready(conn):
    """Reprise sur le montage apres que l'utilisateur a supprime les clips rates.

    Rien a monter n'est pas une panne. L'exiger enfermait la VOD dans une erreur
    qu'AUCUN geste ne pouvait plus lever : supprimer un montage rate est
    justement ce que l'utilisateur fait, et cela devenait irreversible.
    """
    from clipforge.pipeline.preconditions import build_preconditions
    from clipforge.pipeline.steps import build_real_steps

    vod_id = _seed(conn)
    autoclip.step_autoclips(conn, vod_id, {})
    conn.execute("DELETE FROM clips WHERE vod_id=?", (vod_id,))
    conn.commit()
    run_steps(conn, vod_id, build_real_steps(), {}, resume_from="rendering_clips",
              preconditions=build_preconditions())
    assert dbmod.get_vod(conn, vod_id)["status"] == "ready"


def test_the_pipeline_stops_before_making_clips_without_a_scene_profile(conn):
    """Le refus arrive AVANT la fabrication, et la reprise repart de la."""
    from clipforge.pipeline.preconditions import build_preconditions
    from clipforge.pipeline.steps import build_real_steps

    vod_id = _seed(conn, profil=False)
    run_steps(conn, vod_id, build_real_steps(), {}, resume_from="making_clips",
              preconditions=build_preconditions())
    vod = dbmod.get_vod(conn, vod_id)
    assert vod["status"] == "error"
    assert "profil de scène" in vod["error"]
    assert conn.execute("SELECT COUNT(*) FROM clips").fetchone()[0] == 0
    assert vod["error_detail"].startswith("making_clips:")


def test_a_real_render_runs_in_the_analysis_process_without_locking_the_base(
        conn, tmp_path, monkeypatch):
    """Le seul test qui fait tourner le VRAI render_clip_job depuis la boucle.

    Il rouvre la base par son chemin, donc une SECONDE connexion sur le meme
    fichier, dans le meme process que l'etape. Partout ailleurs il est remplace :
    aucun de ces tests ne verrait un verrou, ni un nom de fichier faux, ni cinq
    rendus qui s'ecrasent. Seul ffmpeg est simule — et il ECRIT son fichier de
    sortie, sans quoi la collision de noms resterait invisible — teardown_logging
    avec lui (le vrai fermerait les handlers de pytest).
    """
    from faux_ffmpeg import FauxFfmpeg

    from clipforge.render import montage_plan as montage_plan_mod

    monkeypatch.setattr(config, "teardown_logging", lambda: None)
    monkeypatch.setattr(montage_plan_mod, "measure_loudness", lambda *a, **k: None)
    monkeypatch.setattr(config, "JOBS_DIR", tmp_path / "jobs")
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path / "output")
    faux = FauxFfmpeg()   # ffmpeg pose son fichier : la collision devient reelle
    faux.installer(monkeypatch)

    vod_id = _seed(conn)
    conn.execute("UPDATE vods SET width=1920, height=1080, fps=30 WHERE id=?", (vod_id,))
    conn.commit()
    autoclip.step_autoclips(conn, vod_id, {})
    conn.execute("UPDATE clips SET title='Il manque de mourir très fort' WHERE vod_id=?",
                 (vod_id,))
    conn.commit()
    dbmod.set_vod_status(conn, vod_id, "rendering_clips")

    autoclip.step_autorender(conn, vod_id, {})

    lignes = conn.execute(
        "SELECT status, error, output_path FROM clips WHERE vod_id=? ORDER BY id",
        (vod_id,)).fetchall()
    assert [r["status"] for r in lignes] == ["done"] * 5
    assert all(r["error"] is None for r in lignes)
    noms = [Path(r["output_path"]).name for r in lignes]
    assert all(re.fullmatch(
        r"\d{4}-\d{2}-\d{2} - Il manque de mourir tres fort( \(\d+\))?\.mp4", n) for n in noms)
    # cinq titres identiques le meme jour : cinq FICHIERS, pas un seul reecrit
    # quatre fois avec cinq lignes 'done' qui pointent dessus
    assert len(set(noms)) == 5
    assert all(Path(r["output_path"]).is_file() for r in lignes)
    assert len(faux.calls) == 5
    # la connexion de l'etape ecrit toujours apres les cinq allers-retours
    assert dbmod.get_vod(conn, vod_id)["progress"] == 100.0


def test_the_render_hands_the_journal_back_to_the_pipeline(conn, monkeypatch):
    """render_clip_job rend le journal en sortant (teardown_logging), ce qui est
    juste pour un process de rendu qui meurt derriere. Appele DANS le process
    d'analyse, il laisserait la suite du pipeline sans handler : les erreurs de
    montage suivantes n'atteindraient plus moteur.log, et un echec deviendrait
    muet la ou il compte le plus.
    """
    vod_id = _seed(conn)
    autoclip.step_autoclips(conn, vod_id, {})
    reouvertures = []
    monkeypatch.setattr(config, "setup_logging", lambda: reouvertures.append(True))
    monkeypatch.setattr(autoclip, "render_clip_job", lambda *a: None)
    autoclip.step_autorender(conn, vod_id, {})
    assert len(reouvertures) == 5


def test_the_second_render_failure_still_reaches_the_engine_journal(conn, tmp_path, monkeypatch):
    """Le journal REEL, sur disque : la seule sonde qui voie l'ORDRE des lignes.

    Compter les reouvertures ne prouve rien sur leur PLACE : deplacer
    setup_logging apres la ligne d'erreur laisse le compte a cinq et perd les
    messages. Ici le journal est vrai — CLIPFORGE_DATA temporaire, setup et
    teardown d'origine — et le faux montage rend le journal en sortant, comme le
    vrai le fait dans son `finally`. Si l'etape ne le rouvre pas AVANT de
    journaliser, l'echec du deuxieme clip n'existe nulle part.
    """
    journal = tmp_path / "moteur.log"
    monkeypatch.setattr(config, "LOG_PATH", journal)
    monkeypatch.setattr(config, "setup_logging", SETUP_LOGGING_REEL)

    def montage_qui_echoue(clip_id, *a):
        """Meme squelette que render_clip_job : journal pris a l'entree, rendu en sortant."""
        config.setup_logging()
        try:
            raise RuntimeError(f"panne du montage clip {clip_id}")
        finally:
            config.teardown_logging()

    vod_id = _seed(conn)
    autoclip.step_autoclips(conn, vod_id, {})
    ids = _clip_ids(conn, vod_id)
    monkeypatch.setattr(autoclip, "render_clip_job", montage_qui_echoue)
    try:
        autoclip.step_autorender(conn, vod_id, {})
    finally:
        config.teardown_logging()   # ferme les handlers : le fichier est complet et relachable

    ecrit = journal.read_text(encoding="utf-8")
    assert f"panne du montage clip {ids[1]}" in ecrit   # le DEUXIEME, celui que le bug perdait
    assert f"panne du montage clip {ids[4]}" in ecrit   # et le dernier
    assert sum(1 for c in ids if f"panne du montage clip {c}" in ecrit) == 5
