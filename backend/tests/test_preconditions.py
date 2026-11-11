"""Ce qu'une etape exige pour demarrer, et ce que son refus produit.

Deux niveaux : la mecanique de run_steps (qui refuse, quand, avec quel message)
puis les preconditions reellement declarees pour les etapes du pipeline.
"""
from clipforge import config
from clipforge import db as dbmod
from clipforge.pipeline.preconditions import build_preconditions
from clipforge.pipeline.runner import (
    STEP_ORDER,
    PreconditionError,
    resume_step,
    run_analysis_pipeline,
    run_steps,
)


def test_a_step_whose_precondition_fails_never_runs(conn):
    vod_id = dbmod.create_vod(conn, title="t", source_path="x.mp4")
    appelee = []

    def jamais(conn, vod_id, ctx):
        appelee.append(True)

    steps = [("probing", jamais)]
    preconditions = {"probing": lambda conn, vod_id: "source introuvable"}
    run_steps(conn, vod_id, steps, {}, preconditions=preconditions)
    assert appelee == []
    vod = dbmod.get_vod(conn, vod_id)
    assert vod["status"] == "error"
    assert "source introuvable" in vod["error"]


def test_a_satisfied_precondition_lets_the_step_run(conn):
    vod_id = dbmod.create_vod(conn, title="t", source_path="x.mp4")
    appelee = []
    steps = [("probing", lambda conn, vod_id, ctx: appelee.append(True))]
    run_steps(conn, vod_id, steps, {}, preconditions={"probing": lambda c, v: None})
    assert appelee == [True]
    assert dbmod.get_vod(conn, vod_id)["status"] == "ready"


def test_the_precondition_message_reaches_the_user_not_a_trace(conn):
    vod_id = dbmod.create_vod(conn, title="t", source_path="x.mp4")
    run_steps(conn, vod_id, [("probing", lambda c, v, ctx: None)], {},
              preconditions={"probing": lambda c, v: "disque plein"})
    vod = dbmod.get_vod(conn, vod_id)
    assert "Traceback" not in (vod["error"] or "")
    assert "disque plein" in vod["error"]


def test_a_precondition_may_raise_its_own_refusal(conn):
    """Rendre la raison ou la lever revient au meme : dans les deux cas le
    message arrive tel quel a l'utilisateur, sans pile d'appels."""
    vod_id = dbmod.create_vod(conn, title="t", source_path="x.mp4")

    def refuse(c, v):
        raise PreconditionError("modele de transcription absent")

    run_steps(conn, vod_id, [("transcribing", lambda c, v, ctx: None)], {},
              preconditions={"transcribing": refuse})
    vod = dbmod.get_vod(conn, vod_id)
    assert "modele de transcription absent" in vod["error"]
    assert "Traceback" not in (vod["error_detail"] or "")


def test_a_refused_step_stays_the_resume_point(conn):
    """Un refus ne doit rien couter : une fois la cause levee, la relance
    repart de l'etape refusee et pas du debut. C'est tout l'objet du garde-fou,
    le contraire couterait la re-transcription qu'il pretend eviter."""
    vod_id = dbmod.create_vod(conn, title="t", source_path="x.mp4")
    steps = [(n, lambda c, v, ctx: None) for n in STEP_ORDER]
    run_steps(conn, vod_id, steps, {}, resume_from="segmenting",
              preconditions={"segmenting": lambda c, v: "aucun mot transcrit"})
    assert resume_step(dbmod.get_vod(conn, vod_id)) == "segmenting"


def test_a_skipped_step_is_never_asked_for_its_preconditions(conn):
    """La reprise saute ce qui est deja fait : l'exiger encore bloquerait sur un
    fichier de travail efface depuis, alors que l'etape ne tournera pas."""
    vod_id = dbmod.create_vod(conn, title="t", source_path="x.mp4")
    demandees = []

    def note(nom):
        def controle(c, v):
            """Note le passage et ne refuse rien : le retour implicite vaut None."""
            demandees.append(nom)
        return controle

    steps = [(n, lambda c, v, ctx: None) for n in STEP_ORDER]
    run_steps(conn, vod_id, steps, {}, resume_from="segmenting",
              preconditions={n: note(n) for n in STEP_ORDER})
    assert demandees == ["segmenting", "ai_scoring", "making_clips", "writing_hooks",
                         "rendering_clips"]
    assert dbmod.get_vod(conn, vod_id)["status"] == "ready"


def test_a_precondition_that_crashes_is_reported_like_a_step_failure(conn):
    """Le garde-fou ne peut pas devenir une panne muette : son propre plantage
    garde sa trace ET le nom de l'etape, donc la reprise sait ou repartir."""
    vod_id = dbmod.create_vod(conn, title="t", source_path="x.mp4")
    appelee = []

    def explose(c, v):
        raise OSError("disque debranche")

    run_steps(conn, vod_id, [("probing", lambda c, v, ctx: appelee.append(True))], {},
              preconditions={"probing": explose})
    vod = dbmod.get_vod(conn, vod_id)
    assert appelee == []
    assert vod["status"] == "error"
    assert "Traceback" in vod["error_detail"] and "disque debranche" in vod["error_detail"]
    assert resume_step(vod) == "probing"


def _exigence(nom):
    return build_preconditions()[nom]


def test_every_declared_precondition_guards_a_real_step():
    """Une exigence posee sur un nom d'etape inexistant ne serait jamais lue."""
    assert set(build_preconditions()) <= set(STEP_ORDER)


def test_probing_refuses_a_source_that_is_no_longer_there(conn, tmp_path):
    vod_id = dbmod.create_vod(conn, title="t", source_path=str(tmp_path / "partie.mp4"))
    assert "introuvable" in _exigence("probing")(conn, vod_id)


def test_probing_accepts_a_source_still_on_disk(conn, tmp_path):
    source = tmp_path / "v.mp4"
    source.write_bytes(b"x")
    vod_id = dbmod.create_vod(conn, title="t", source_path=str(source))
    assert _exigence("probing")(conn, vod_id) is None


def test_a_vod_not_downloaded_yet_is_not_blamed_for_a_missing_source(conn):
    """source_path est rempli par le telechargement : l'exiger avant qu'il ait
    eu lieu ferait echouer l'analyse au lieu de la laisser commencer."""
    vod_id = dbmod.create_vod(conn, title="t", twitch_url="https://twitch.tv/videos/1")
    assert _exigence("probing")(conn, vod_id) is None


def test_transcribing_refuses_without_the_extracted_audio(conn, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "JOBS_DIR", tmp_path)
    vod_id = dbmod.create_vod(conn, title="t", source_path="x.mp4")
    assert "audio" in _exigence("transcribing")(conn, vod_id)
    wav = tmp_path / f"vod{vod_id}" / "audio.wav"
    wav.parent.mkdir(parents=True)
    wav.write_bytes(b"x")
    assert _exigence("transcribing")(conn, vod_id) is None


def test_segmenting_is_never_refused_for_a_lack_of_words(conn):
    """Le decoupage n'exige aucun mot, et le lui demander etait sans retour.

    L'etape tourne sur les seuls signaux audio et chat (preuve :
    test_segment_step_needs_no_word_to_produce_candidates, test_runner.py). Une
    transcription vide — micro sur la mauvaise entree, sequence musicale, VAD
    qui ne rend rien — mettait alors la VOD en erreur DEFINITIVE : le refus
    repointe la reprise sur segmenting, step_transcribe ressort aussitot sur
    words_complete=1, et aucun bouton de l'interface ne rejoue la transcription.
    Seule sortie pour l'utilisateur : supprimer la VOD et la reimporter.
    """
    vod_id = dbmod.create_vod(conn, title="t", source_path="x.mp4")
    conn.execute("UPDATE vods SET words_complete=1 WHERE id=?", (vod_id,))
    conn.commit()
    jouees = []
    steps = [(n, lambda c, v, ctx, n=n: jouees.append(n)) for n in STEP_ORDER]
    run_steps(conn, vod_id, steps, {}, resume_from="segmenting",
              preconditions=build_preconditions())
    assert jouees == ["segmenting", "ai_scoring", "making_clips", "writing_hooks",
                      "rendering_clips"]
    assert dbmod.get_vod(conn, vod_id)["status"] == "ready"


def _vod_decoupee(conn, *, segments: int = 3, profil: bool = True) -> int:
    """Une VOD avec ses candidats, et le profil de scene qui sert a les cadrer."""
    vod_id = dbmod.create_vod(conn, title="t", source_path="x.mp4")
    for rang in range(1, segments + 1):
        conn.execute(
            "INSERT INTO segments (vod_id, start_s, end_s, score, rank) VALUES (?,?,?,?,?)",
            (vod_id, rang * 100.0, rang * 100.0 + 30.0, 0.9, rang))
    if profil:
        conn.execute(
            "INSERT INTO scene_profiles (name, cam_x, cam_y, cam_w, cam_h, "
            "game_x, game_y, game_w, game_h) VALUES ('scene',0,0,640,340,640,0,868,1080)")
    conn.commit()
    return vod_id


def test_making_clips_asks_for_the_scene_profile_to_be_traced_once(conn):
    """Le seul geste que le mode auto reclame encore : le message le dit tel quel."""
    vod_id = _vod_decoupee(conn, profil=False)
    raison = _exigence("making_clips")(conn, vod_id)
    assert raison == ("Trace un profil de scène dans l'éditeur une fois — "
                      "il s'appliquera ensuite tout seul.")


def test_making_clips_requires_nothing_without_a_single_candidate(conn):
    """Zero candidat n'est pas un manque que l'utilisateur puisse combler.

    Reclamer un profil de scene la serait une impasse doublement absurde : le
    tracer ne ferait apparaitre aucun candidat, et le refus enfermerait la VOD
    dans une erreur dont la reprise ne rejoue jamais le decoupage. Rien a
    fabriquer, donc rien a exiger : les trois etapes ne feront rien et la VOD
    finira 'ready', comme avant la V5.
    """
    assert _exigence("making_clips")(conn, _vod_decoupee(conn, segments=0,
                                                         profil=False)) is None


def test_making_clips_accepts_candidates_and_a_profile(conn):
    assert _exigence("making_clips")(conn, _vod_decoupee(conn)) is None


def test_making_clips_requires_nothing_when_the_count_is_zero(conn):
    """Mode auto coupe : les etapes ne font rien, et rien ne peut donc manquer.
    Exiger un profil mettrait la VOD en ERREUR au lieu de la laisser finir."""
    vod_id = _vod_decoupee(conn, profil=False)
    dbmod.set_setting(conn, "auto_clip_count", "0")
    assert _exigence("making_clips")(conn, vod_id) is None


def test_rendering_clips_and_writing_hooks_declare_no_precondition(conn):
    """Ni l'une ni l'autre ne peut exiger quoi que ce soit de levable.

    write_clips ne leve jamais et se replie seul. Et « aucun clip a monter »
    n'est pas une panne : step_autorender rend la main proprement sur un lot
    vide, alors qu'un refus enfermait dans une erreur DEFINITIVE la VOD dont
    l'utilisateur venait de supprimer les montages rates — le geste normal
    devenait irreversible. Une exigence ne se pose que sur ce que l'utilisateur
    peut lever.
    """
    declarees = build_preconditions()
    assert "rendering_clips" not in declarees
    assert "writing_hooks" not in declarees


def test_the_real_pipeline_carries_its_preconditions(tmp_path, monkeypatch):
    """Declarer ne sert a rien si le point d'entree du process enfant ne
    transmet pas : c'est exactement le maillon qui manquait au point de reprise
    en V3, et qui a coute une re-transcription de 2 h 41."""
    db_path = tmp_path / "pipeline.db"
    conn = dbmod.get_db(db_path)
    vod_id = dbmod.create_vod(conn, title="t", source_path=str(tmp_path / "absente.mp4"))
    conn.close()
    calls = []
    monkeypatch.setattr(
        "clipforge.pipeline.steps.build_real_steps",
        lambda: [(n, lambda c, v, ctx, n=n: calls.append(n)) for n in STEP_ORDER])
    run_analysis_pipeline(vod_id, str(db_path))
    assert "probing" not in calls
    conn = dbmod.get_db(db_path)
    assert "introuvable" in dbmod.get_vod(conn, vod_id)["error"]
    conn.close()


def test_making_clips_asks_which_profile_when_several_exist(conn):
    """Deux profils sans choix : le message dit lequel nommer, pas d'en tracer un."""
    vod_id = _vod_decoupee(conn)
    conn.execute(
        "INSERT INTO scene_profiles (name, cam_x, cam_y, cam_w, cam_h, "
        "game_x, game_y, game_w, game_h) VALUES ('obs',1872,1032,640,360,0,0,2560,1440)")
    conn.commit()
    assert _exigence("making_clips")(conn, vod_id) == (
        "Plusieurs profils de scène existent : choisis celui des clips "
        "automatiques dans Réglages, « Profil des clips automatiques ».")


def test_making_clips_takes_the_profile_of_the_vod_despite_an_open_choice(conn):
    """Pont OBS : la VOD porte son cadrage, le choix global peut rester ouvert."""
    vod_id = _vod_decoupee(conn)
    cur = conn.execute(
        "INSERT INTO scene_profiles (name, cam_x, cam_y, cam_w, cam_h, "
        "game_x, game_y, game_w, game_h) VALUES ('obs',1874,1042,636,340,701,0,1158,1440)")
    conn.execute("UPDATE vods SET profile_id=? WHERE id=?", (cur.lastrowid, vod_id))
    conn.commit()
    assert _exigence("making_clips")(conn, vod_id) is None


def test_making_clips_takes_the_profile_named_by_auto_profile(conn):
    vod_id = _vod_decoupee(conn)
    conn.execute(
        "INSERT INTO scene_profiles (name, cam_x, cam_y, cam_w, cam_h, "
        "game_x, game_y, game_w, game_h) VALUES ('obs',1872,1032,640,360,0,0,2560,1440)")
    conn.commit()
    dbmod.set_setting(conn, "auto_profile", "obs")
    assert _exigence("making_clips")(conn, vod_id) is None


def test_making_clips_says_when_the_named_profile_is_gone(conn):
    vod_id = _vod_decoupee(conn)
    dbmod.set_setting(conn, "auto_profile", "disparu")
    raison = _exigence("making_clips")(conn, vod_id)
    assert raison is not None and "« disparu »" in raison
