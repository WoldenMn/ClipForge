import concurrent.futures
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

FIXTURE = Path(__file__).parent / "fixtures" / "fixture.mp4"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CLIPFORGE_DATA", str(tmp_path))
    import importlib

    from clipforge import config
    importlib.reload(config)
    from clipforge import main as mainmod
    importlib.reload(mainmod)
    with TestClient(mainmod.app, base_url="http://127.0.0.1:8765") as c:
        yield c


def test_import_local_and_list(client, tmp_path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    r = client.post("/api/vods/import-local", json={"path": str(video)})
    assert r.status_code == 200
    vod_id = r.json()["id"]
    listing = client.get("/api/vods").json()
    assert any(v["id"] == vod_id for v in listing)


def test_import_local_missing_file_400(client):
    r = client.post("/api/vods/import-local", json={"path": r"C:\nexiste\pas.mp4"})
    assert r.status_code == 400


def test_import_local_bad_extension_400(client, tmp_path):
    notes = tmp_path / "notes.txt"
    notes.write_text("pas une video")
    r = client.post("/api/vods/import-local", json={"path": str(notes)})
    assert r.status_code == 400
    assert "Extension non supportee" in r.json()["detail"]


def test_import_twitch_creates_vod_with_id(client):
    r = client.post("/api/vods/import-twitch",
                    json={"url": "https://www.twitch.tv/videos/612942303"})
    assert r.status_code == 200
    assert r.json()["twitch_vod_id"] == "612942303"


def test_analyze_missing_vod_404(client):
    r = client.post("/api/vods/999/analyze")
    assert r.status_code == 404


def test_vod_file_twitch_without_source_404(client):
    r = client.post("/api/vods/import-twitch",
                    json={"url": "https://www.twitch.tv/videos/612942303"})
    vod_id = r.json()["id"]
    r = client.get(f"/api/vods/{vod_id}/file")
    assert r.status_code == 404


def test_analyze_conflict_409(client):
    r = client.post("/api/vods/import-twitch",
                    json={"url": "https://www.twitch.tv/videos/612942303"})
    vod_id = r.json()["id"]
    client.app.state.running[vod_id] = concurrent.futures.Future()
    r = client.post(f"/api/vods/{vod_id}/analyze")
    assert r.status_code == 409


@pytest.mark.parametrize("statut", ["queued", "rendering"])
def test_analyze_refuses_while_a_clip_of_the_vod_is_being_mounted(client, tmp_path, statut):
    """Fix round 3 (F1) : reprise a l'etape de montage pendant que le pool rend
    les memes clips (« Tout relancer » puis « Relancer l'analyse ») : le clip
    etait livre deux fois, parfois corrompu et marque 'done'. Le message dit
    quoi attendre, et la VOD ne repart pas en 'queued'."""
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    vod_id = client.post("/api/vods/import-local", json={"path": str(video)}).json()["id"]

    from clipforge import config
    from clipforge import db as dbmod
    conn = dbmod.get_db(config.DB_PATH)
    try:
        conn.execute("INSERT INTO clips (vod_id, start_s, end_s, origin, status) "
                     "VALUES (?,1.0,2.0,'auto',?)", (vod_id, statut))
        conn.commit()
    finally:
        conn.close()

    r = client.post(f"/api/vods/{vod_id}/analyze")
    assert r.status_code == 409, r.text
    assert "1 clip(s) de cette VOD en cours de montage" in r.json()["detail"]
    assert "relance l'analyse quand ils auront fini" in r.json()["detail"]
    assert client.get(f"/api/vods/{vod_id}").json()["status"] == "imported"

    conn = dbmod.get_db(config.DB_PATH)
    try:
        conn.execute("UPDATE clips SET status='done'")
        conn.commit()
    finally:
        conn.close()
    client.app.state.pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    assert client.post(f"/api/vods/{vod_id}/analyze").status_code == 200


def test_analyze_sets_status_queued_immediately(client, tmp_path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    r = client.post("/api/vods/import-local", json={"path": str(video)})
    vod_id = r.json()["id"]
    r = client.post(f"/api/vods/{vod_id}/analyze")
    assert r.status_code == 200
    vod = client.get(f"/api/vods/{vod_id}").json()
    assert vod["status"] == "queued"


def test_analyze_clears_the_previous_error_and_progression(client, tmp_path, monkeypatch):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    vod_id = client.post("/api/vods/import-local", json={"path": str(video)}).json()["id"]

    from clipforge import config
    from clipforge import db as dbmod
    conn = dbmod.get_db(config.DB_PATH)
    dbmod.set_vod_error(conn, vod_id, "court", "transcribing: technique")
    conn.execute("UPDATE vods SET step_label='Transcription', eta_s=42.0 WHERE id=?", (vod_id,))
    conn.commit()
    conn.close()

    client.app.state.pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    monkeypatch.setattr("clipforge.api.vods.run_analysis_pipeline",
                        lambda vid, db_path, resume_from=None: None)
    assert client.post(f"/api/vods/{vod_id}/analyze").status_code == 200

    vod = client.get(f"/api/vods/{vod_id}").json()
    assert vod["error"] is None and vod["error_detail"] is None
    assert vod["step_label"] is None and vod["eta_s"] is None


def test_analyze_frames_the_vod_with_the_named_profile_and_a_relaunch_keeps_it(
        client, tmp_path, monkeypatch):
    """Pont OBS : ?profile= pose le cadrage de CETTE VOD ; « Relancer
    l'analyse », qui n'envoie rien, ne le perd pas."""
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    vod_id = client.post("/api/vods/import-local", json={"path": str(video)}).json()["id"]
    obs = client.post("/api/profiles", json={
        "name": "WOLDENM OBS 1440p", "cam_x": 1874, "cam_y": 1042, "cam_w": 636,
        "cam_h": 340, "game_x": 701, "game_y": 0, "game_w": 1158, "game_h": 1440}).json()
    client.app.state.pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    monkeypatch.setattr("clipforge.api.vods.run_analysis_pipeline",
                        lambda vid, db_path, resume_from=None: None)

    r = client.post(f"/api/vods/{vod_id}/analyze", params={"profile": "WOLDENM OBS 1440p"})
    assert r.status_code == 200, r.text
    assert client.get(f"/api/vods/{vod_id}").json()["profile_id"] == obs["id"]

    client.app.state.running.pop(vod_id)
    assert client.post(f"/api/vods/{vod_id}/analyze").status_code == 200
    assert client.get(f"/api/vods/{vod_id}").json()["profile_id"] == obs["id"]


def test_analyze_refuses_an_unknown_profile_without_queuing(client, tmp_path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    vod_id = client.post("/api/vods/import-local", json={"path": str(video)}).json()["id"]
    r = client.post(f"/api/vods/{vod_id}/analyze", params={"profile": "Inconnu"})
    assert r.status_code == 400
    assert r.json()["detail"] == "Profil de scene inconnu : Inconnu"
    vod = client.get(f"/api/vods/{vod_id}").json()
    assert vod["status"] == "imported" and vod["profile_id"] is None


def _analyze_and_capture(client, monkeypatch, vod_id: int) -> dict:
    """Lance l'analyse avec un pipeline factice et rend les arguments recus.

    L'endpoint efface error/error_detail avant de soumettre : ce que le pipeline
    recoit est le SEUL temoin du point de reprise reellement transmis.
    """
    import threading

    vu: dict = {}
    fini = threading.Event()

    def faux_pipeline(vid, db_path, resume_from=None):
        vu.update(vod_id=vid, resume_from=resume_from)
        fini.set()

    monkeypatch.setattr("clipforge.api.vods.run_analysis_pipeline", faux_pipeline)
    client.app.state.pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    assert client.post(f"/api/vods/{vod_id}/analyze").status_code == 200
    assert fini.wait(10), "le pipeline n'a jamais ete appele"
    return vu


def test_analyze_resumes_at_the_step_that_failed(client, tmp_path, monkeypatch):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    vod_id = client.post("/api/vods/import-local", json={"path": str(video)}).json()["id"]

    from clipforge import config
    from clipforge import db as dbmod
    conn = dbmod.get_db(config.DB_PATH)
    dbmod.set_vod_error(conn, vod_id, "Decoupage : echec", "segmenting: boom")
    conn.close()

    assert _analyze_and_capture(client, monkeypatch, vod_id)["resume_from"] == "segmenting"


def test_analyze_sends_a_poisoned_row_back_to_the_healing(client, tmp_path, monkeypatch):
    """Le GESTE REEL du user sur sa ligne du 2026-08-30 : « Reprendre l'analyse ».

    La ligne dit `probing:` (le run precedent l'a empoisonnee) au-dessus d'un
    MPEG-TS brut. Ce que l'endpoint transmet au process enfant est le seul
    temoin : il valait `probing`, donc telechargement et remux etaient sautes et
    la reparation de de8258f restait inatteignable — une seconde d'analyse, la
    meme erreur, indefiniment.
    """
    from clipforge import config
    from clipforge import db as dbmod
    from clipforge.pipeline import remux

    config.DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
    source = config.DOWNLOADS_DIR / "v2836283171.mp4"
    source.write_bytes((bytes([remux.TS_SYNC]) + b"\x00" * (remux.TS_PACKET - 1)) * 8)
    conn = dbmod.get_db(config.DB_PATH)
    vod_id = dbmod.create_vod(conn, title="VOD 2836283171", source_path=str(source),
                              twitch_url="https://www.twitch.tv/videos/2836283171")
    dbmod.set_vod_error(conn, vod_id, "Lecture métadonnées : Durée indisponible",
                        "probing: ProbeError: Durée indisponible")
    conn.close()

    assert _analyze_and_capture(client, monkeypatch, vod_id)["resume_from"] == "remuxing"


def test_a_dead_child_process_leaves_no_resume_point(client, tmp_path):
    """Mort au niveau process : le detail ecrit ne doit nommer aucune etape.

    Le format vient d'un appel reel a _log_pool_failure, jamais d'une copie dans
    le test : si son prefixe devenait un nom d'etape, la reprise sauterait des
    etapes que le pipeline n'a jamais executees.
    """
    from clipforge import config
    from clipforge import db as dbmod
    from clipforge.api.vods import _log_pool_failure
    from clipforge.pipeline.runner import resume_step

    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    vod_id = client.post("/api/vods/import-local", json={"path": str(video)}).json()["id"]

    mort = concurrent.futures.Future()
    mort.set_exception(RuntimeError("processus enfant tue"))
    _log_pool_failure(vod_id, mort)

    conn = dbmod.get_db_light(config.DB_PATH)
    try:
        vod = dbmod.get_vod(conn, vod_id)
    finally:
        conn.close()
    assert vod["status"] == "error" and vod["error_detail"]
    assert resume_step(vod) is None


def test_analyze_replays_everything_when_no_step_failed(client, tmp_path, monkeypatch):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    vod_id = client.post("/api/vods/import-local", json={"path": str(video)}).json()["id"]

    assert _analyze_and_capture(client, monkeypatch, vod_id)["resume_from"] is None


def test_purge_orphan_job_dir_on_startup(tmp_path, monkeypatch):
    monkeypatch.setenv("CLIPFORGE_DATA", str(tmp_path))
    import importlib

    from clipforge import config
    importlib.reload(config)
    from clipforge import main as mainmod
    importlib.reload(mainmod)

    orphan = config.JOBS_DIR / "vod99999"
    orphan.mkdir(parents=True)
    (orphan / "frame_1.0.png").write_bytes(b"x")

    with TestClient(mainmod.app):
        assert not orphan.exists()


@pytest.mark.integration
def test_vod_thumb_serves_a_jpeg(client):
    vid = client.post("/api/vods/import-local", json={"path": str(FIXTURE)}).json()["id"]
    r = client.get(f"/api/vods/{vid}/thumb", params={"t": 5.0})
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/jpeg"
    assert len(r.content) > 0


@pytest.mark.integration
def test_vod_thumb_beyond_duration_404(client):
    vid = client.post("/api/vods/import-local", json={"path": str(FIXTURE)}).json()["id"]
    assert client.get(f"/api/vods/{vid}/thumb", params={"t": 9999}).status_code == 404


def test_vod_thumb_unknown_vod_404(client):
    assert client.get("/api/vods/99999/thumb", params={"t": 1.0}).status_code == 404


def test_delete_vod_removes_rows_and_job_files(client, tmp_path):
    from clipforge import config
    from clipforge import db as dbmod

    video = tmp_path / "v.mp4"
    video.write_bytes(b"x" * 3 * 1048576)
    vid = client.post("/api/vods/import-local", json={"path": str(video)}).json()["id"]

    conn = dbmod.get_db(config.DB_PATH)
    conn.execute("INSERT INTO words VALUES (?,?,?,?,?,?)", (vid, 0, 1.0, 1.4, "salut", 0.9))
    conn.execute("INSERT INTO signals VALUES (?,?,?,?)", (vid, "audio", 1.0, 0.5))
    conn.execute("INSERT INTO segments (vod_id, start_s, end_s, score) VALUES (?,?,?,?)",
                 (vid, 1.0, 21.0, 0.8))
    conn.execute("INSERT INTO clips (vod_id, start_s, end_s) VALUES (?,?,?)", (vid, 1.0, 21.0))
    conn.commit()
    conn.close()

    jobs = config.JOBS_DIR / f"vod{vid}"
    (jobs / "thumbs").mkdir(parents=True, exist_ok=True)
    (jobs / "audio.wav").write_bytes(b"x")

    r = client.delete(f"/api/vods/{vid}")
    assert r.status_code == 200
    body = r.json()
    assert body["deleted"] is True
    assert body["source_path"] == str(video)
    assert body["source_size_mo"] == 3
    assert video.is_file()  # la video source n'est JAMAIS supprimee par cet endpoint
    assert not jobs.exists()
    assert client.get(f"/api/vods/{vid}").status_code == 404

    conn = dbmod.get_db_light(config.DB_PATH)
    restants = {table: conn.execute(
        f"SELECT COUNT(*) AS n FROM {table} WHERE vod_id=?", (vid,)).fetchone()["n"]
        for table in ("words", "signals", "segments", "clips")}
    conn.close()
    assert restants == {"words": 0, "signals": 0, "segments": 0, "clips": 0}


def test_delete_vod_without_source_file_reports_no_size(client):
    vid = client.post("/api/vods/import-twitch",
                      json={"url": "https://www.twitch.tv/videos/612942303"}).json()["id"]
    body = client.delete(f"/api/vods/{vid}").json()
    assert body["deleted"] is True
    assert body["source_path"] == ""
    assert body["source_size_mo"] is None


def test_delete_vod_unknown_404(client):
    assert client.delete("/api/vods/99999").status_code == 404


def test_vod_words_filters_interval(client, tmp_path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    vod_id = client.post("/api/vods/import-local", json={"path": str(video)}).json()["id"]
    from clipforge import config
    from clipforge import db as dbmod
    conn = dbmod.get_db(config.DB_PATH)
    conn.executemany(
        "INSERT INTO words (vod_id, idx, start_s, end_s, text, probability) VALUES (?,?,?,?,?,?)",
        [
            (vod_id, 0, 1.0, 1.4, "salut", 0.9),
            (vod_id, 1, 5.0, 5.4, "tout", 0.9),
            (vod_id, 2, 50.0, 50.4, "hors intervalle", 0.9),
        ],
    )
    conn.commit()
    conn.close()
    r = client.get(f"/api/vods/{vod_id}/words", params={"start_s": 0, "end_s": 10})
    assert r.status_code == 200
    words = r.json()
    assert words == [
        {"start": 1.0, "end": 1.4, "text": "salut"},
        {"start": 5.0, "end": 5.4, "text": "tout"},
    ]


def test_preview_unknown_vod_404(client):
    assert client.get("/api/vods/9999/preview", params={"t": 1.0}).status_code == 404


def test_preview_negative_instant_422(client, tmp_path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    vod_id = client.post("/api/vods/import-local", json={"path": str(video)}).json()["id"]
    assert client.get(f"/api/vods/{vod_id}/preview", params={"t": -1}).status_code == 422


@pytest.mark.integration
def test_preview_serves_a_playable_extract(client, tmp_path):
    """L'extrait est servi en video/mp4 et sort du cache au second appel."""
    video = tmp_path / "v.mp4"
    video.write_bytes(FIXTURE.read_bytes())
    vod_id = client.post("/api/vods/import-local", json={"path": str(video)}).json()["id"]
    r = client.get(f"/api/vods/{vod_id}/preview", params={"t": 5.0})
    assert r.status_code == 200
    assert r.headers["content-type"] == "video/mp4"
    assert len(r.content) > 2048
    from clipforge import config
    from clipforge.pipeline.previews import preview_path
    assert preview_path(config.JOBS_DIR, vod_id, 5.0).is_file()
    assert client.get(f"/api/vods/{vod_id}/preview", params={"t": 5.0}).content == r.content


@pytest.mark.integration
def test_preview_beyond_duration_404(client, tmp_path):
    video = tmp_path / "v.mp4"
    video.write_bytes(FIXTURE.read_bytes())
    vod_id = client.post("/api/vods/import-local", json={"path": str(video)}).json()["id"]
    r = client.get(f"/api/vods/{vod_id}/preview", params={"t": 9999.0})
    assert r.status_code == 404


def _clip_de_vod(vod_id: int, statut: str) -> int:
    from clipforge import config
    from clipforge import db as dbmod
    conn = dbmod.get_db(config.DB_PATH)
    try:
        clip_id = conn.execute("INSERT INTO clips (vod_id, start_s, end_s, origin, status) "
                               "VALUES (?,1.0,2.0,'auto',?)", (vod_id, statut)).lastrowid
        conn.commit()
        return clip_id
    finally:
        conn.close()


def _vod_importee(client, tmp_path, nom: str) -> int:
    video = tmp_path / nom
    video.write_bytes(b"x")
    return client.post("/api/vods/import-local", json={"path": str(video)}).json()["id"]


def test_analyze_ignores_a_clip_being_mounted_on_another_vod(client, tmp_path):
    """Re-revue du fix round 3 : retirer `vod_id=?` de la garde laissait la suite
    verte. Un montage en cours ailleurs ne doit pas bloquer l'analyse d'une VOD
    qui n'a rien en cours."""
    libre = _vod_importee(client, tmp_path, "libre.mp4")
    occupee = _vod_importee(client, tmp_path, "occupee.mp4")
    _clip_de_vod(occupee, "rendering")

    assert client.post(f"/api/vods/{occupee}/analyze").status_code == 409
    assert client.post(f"/api/vods/{libre}/analyze").status_code == 200


def test_a_dead_child_process_frees_the_clips_it_left_mounting(client, tmp_path):
    """Re-revue du fix round 3 : un worker mort pendant l'etape de montage
    laissait ses clips en 'queued'/'rendering' pour toujours, et la garde
    d'analyse les comptait : 409 a jamais, pour un travail qui ne tournait plus
    (mesure du relecteur : trois refus de suite, rien en cours)."""
    from clipforge import config
    from clipforge import db as dbmod
    from clipforge.api.vods import _log_pool_failure

    vod_id = _vod_importee(client, tmp_path, "v.mp4")
    en_file = _clip_de_vod(vod_id, "queued")
    en_rendu = _clip_de_vod(vod_id, "rendering")
    livre = _clip_de_vod(vod_id, "done")

    mort = concurrent.futures.Future()
    mort.set_exception(RuntimeError("processus enfant tue"))
    _log_pool_failure(vod_id, mort)

    conn = dbmod.get_db(config.DB_PATH)
    try:
        lignes = {r["id"]: r for r in conn.execute("SELECT id, status, error FROM clips")}
    finally:
        conn.close()
    assert lignes[en_file]["status"] == "error" and lignes[en_rendu]["status"] == "error"
    assert "relance le rendu" in lignes[en_rendu]["error"], lignes[en_rendu]["error"]
    assert lignes[livre]["status"] == "done", "un clip livre n'est pas touche"
    client.app.state.pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    assert client.post(f"/api/vods/{vod_id}/analyze").status_code == 200
