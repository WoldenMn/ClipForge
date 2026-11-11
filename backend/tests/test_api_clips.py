import concurrent.futures
import json

import pytest
from fastapi.testclient import TestClient


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


def _seed_segment(client, tmp_path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    vod = client.post("/api/vods/import-local", json={"path": str(video)}).json()
    from clipforge import config
    from clipforge import db as dbmod
    conn = dbmod.get_db(config.DB_PATH)
    conn.execute("INSERT INTO segments (vod_id, start_s, end_s, score) VALUES (?,?,?,?)",
                 (vod["id"], 5.0, 35.0, 0.8))
    conn.commit()
    seg_id = conn.execute("SELECT id FROM segments").fetchone()["id"]
    conn.close()
    return vod["id"], seg_id


def test_clip_lifecycle(client, tmp_path):
    _vod_id, seg_id = _seed_segment(client, tmp_path)
    clip = client.post("/api/clips", json={"segment_id": seg_id}).json()
    assert clip["status"] == "draft" and clip["start_s"] == 5.0
    patched = client.patch(f"/api/clips/{clip['id']}",
                           json={"title": "Mon clip", "start_s": 6.0}).json()
    assert patched["title"] == "Mon clip" and patched["start_s"] == 6.0
    r = client.post(f"/api/clips/{clip['id']}/render")
    assert r.status_code == 400  # pas de profil de scene -> erreur explicite


def test_profile_upsert_and_settings(client):
    p = client.post("/api/profiles", json={
        "name": "Scene principale", "cam_x": 24, "cam_y": 800, "cam_w": 480, "cam_h": 258,
        "game_x": 526, "game_y": 0, "game_w": 868, "game_h": 1080}).json()
    assert p["id"] >= 1
    s = client.patch("/api/settings", json={"values": {"ai_enabled": "1"}}).json()
    assert s["ai_enabled"] == "1"


def test_patch_settings_negative_weight_422(client):
    r = client.patch("/api/settings", json={"values": {"weight_audio": "-2"}})
    assert r.status_code == 422


def test_patch_settings_unknown_key_422(client):
    r = client.patch("/api/settings", json={"values": {"clef_inconnue": "x"}})
    assert r.status_code == 422


def test_patch_settings_relative_output_dir_422(client):
    r = client.patch("/api/settings", json={"values": {"output_dir": "sortie\\clips"}})
    assert r.status_code == 422


def test_patch_settings_unknown_whisper_model_422(client):
    r = client.patch("/api/settings", json={"values": {"whisper_model": "n-importe-quoi"}})
    assert r.status_code == 422


def test_patch_settings_invalid_subtitle_preset_422(client):
    r = client.patch("/api/settings",
                     json={"values": {"subtitle_style_default": "{pas du json"}})
    assert r.status_code == 422
    r = client.patch("/api/settings",
                     json={"values": {"subtitle_style_default": '{"primary_hex": "rouge"}'}})
    assert r.status_code == 422


def test_subtitle_preset_applique_aux_nouveaux_clips(client, tmp_path):
    _, seg_id = _seed_segment(client, tmp_path)
    sans = client.post("/api/clips", json={"segment_id": seg_id}).json()
    assert sans["subtitle_style"] is None  # reglage vide = comportement d'avant

    preset = json.dumps({"font_size": 72, "primary_hex": "#00FF00", "uppercase": False})
    assert client.patch("/api/settings",
                        json={"values": {"subtitle_style_default": preset}}).status_code == 200
    avec = client.post("/api/clips", json={"segment_id": seg_id}).json()
    assert json.loads(avec["subtitle_style"])["primary_hex"] == "#00FF00"

    # Le clip cree AVANT le prereglage garde le sien : changer le defaut ne
    # doit jamais reecrire un clip que l'utilisateur a deja regle.
    assert client.get(f"/api/clips/{sans['id']}").json()["subtitle_style"] is None


def _profile(client, name):
    return client.post("/api/profiles", json={
        "name": name, "cam_x": 0, "cam_y": 0, "cam_w": 480, "cam_h": 258,
        "game_x": 0, "game_y": 0, "game_w": 868, "game_h": 1080}).json()


def test_profil_de_scene_unique_applique_aux_nouveaux_clips(client, tmp_path):
    _, seg_id = _seed_segment(client, tmp_path)
    aucun = client.post("/api/clips", json={"segment_id": seg_id}).json()
    assert aucun["profile_id"] is None  # aucun profil en base : rien a appliquer

    seul = _profile(client, "Plateau")
    auto = client.post("/api/clips", json={"segment_id": seg_id}).json()
    assert auto["profile_id"] == seul["id"]  # choix non ambigu : ne pas le poser

    _profile(client, "Plateau invite")
    ambigu = client.post("/api/clips", json={"segment_id": seg_id}).json()
    assert ambigu["profile_id"] is None  # deux profils : a l'utilisateur de trancher

    # Le clip cree quand le choix etait unique garde son profil : ajouter un
    # second profil ne reecrit rien, comme pour le prereglage de sous-titres.
    assert client.get(f"/api/clips/{auto['id']}").json()["profile_id"] == seul["id"]


def test_patch_clip_invalid_bounds_400(client, tmp_path):
    _, seg_id = _seed_segment(client, tmp_path)
    clip = client.post("/api/clips", json={"segment_id": seg_id}).json()
    r = client.patch(f"/api/clips/{clip['id']}", json={"end_s": 4.0})
    assert r.status_code == 400  # end_s <= start_s existant (5.0)
    assert "end_s doit etre > start_s" in r.json()["detail"]
    r = client.patch(f"/api/clips/{clip['id']}", json={"start_s": 35.0})
    assert r.status_code == 400  # start_s >= end_s existant (35.0)
    r = client.patch(f"/api/clips/{clip['id']}", json={"start_s": 20.0, "end_s": 10.0})
    assert r.status_code == 400
    unchanged = client.get(f"/api/clips/{clip['id']}").json()
    assert unchanged["start_s"] == 5.0 and unchanged["end_s"] == 35.0


def test_profile_zero_width_422(client):
    r = client.post("/api/profiles", json={
        "name": "Zone nulle", "cam_x": 0, "cam_y": 0, "cam_w": 0, "cam_h": 258,
        "game_x": 0, "game_y": 0, "game_w": 868, "game_h": 1080})
    assert r.status_code == 422  # gt=0 sur les largeurs/hauteurs (Pydantic)
    r = client.post("/api/profiles", json={
        "name": "Coord negative", "cam_x": -1, "cam_y": 0, "cam_w": 480, "cam_h": 258,
        "game_x": 0, "game_y": 0, "game_w": 868, "game_h": 1080})
    assert r.status_code == 422  # ge=0 sur les coordonnees (Pydantic)


def test_validate_profile_against_source():
    from clipforge.render.queue import validate_profile_against_source

    profile_1440p = {"cam_x": 0, "cam_y": 0, "cam_w": 2560, "cam_h": 1440,
                     "game_x": 0, "game_y": 0, "game_w": 2560, "game_h": 1440}
    with pytest.raises(ValueError, match="incompatible avec la source"):
        validate_profile_against_source(profile_1440p, 1280, 720)

    profile_overflow_1px = {
        "cam_x": 641, "cam_y": 361, "cam_w": 640, "cam_h": 360,
        "game_x": 0, "game_y": 0, "game_w": 1280, "game_h": 720,
    }
    # tolerance 2 px -> pas d'erreur
    validate_profile_against_source(profile_overflow_1px, 1280, 720)


def test_render_conflict_409(client, tmp_path):
    _, seg_id = _seed_segment(client, tmp_path)
    clip = client.post("/api/clips", json={"segment_id": seg_id}).json()
    profile = client.post("/api/profiles", json={
        "name": "Scene render", "cam_x": 0, "cam_y": 0, "cam_w": 480, "cam_h": 258,
        "game_x": 0, "game_y": 0, "game_w": 868, "game_h": 1080}).json()
    client.patch(f"/api/clips/{clip['id']}", json={"profile_id": profile["id"]})
    client.app.state.rendering[clip["id"]] = concurrent.futures.Future()
    r = client.post(f"/api/clips/{clip['id']}/render")
    assert r.status_code == 409  # rendu deja en cours (future injectee, meme technique que vods)


def test_patch_clip_subtitle_style_invalid_json_422(client, tmp_path):
    _, seg_id = _seed_segment(client, tmp_path)
    clip = client.post("/api/clips", json={"segment_id": seg_id}).json()
    r = client.patch(f"/api/clips/{clip['id']}", json={"subtitle_style": "{not json"})
    assert r.status_code == 422
    r = client.patch(f"/api/clips/{clip['id']}",
                     json={"subtitle_style": '{"font_size": "pas un entier"}'})
    assert r.status_code == 422


def test_patch_clip_words_json_extra_key_422(client, tmp_path):
    _, seg_id = _seed_segment(client, tmp_path)
    clip = client.post("/api/clips", json={"segment_id": seg_id}).json()
    words = json.dumps([{"start": 0.0, "end": 0.4, "text": "salut", "probability": 0.9}])
    r = client.patch(f"/api/clips/{clip['id']}", json={"words_json": words})
    assert r.status_code == 422


def test_open_output(client, monkeypatch, tmp_path):
    # monkeypatch os.startfile : sinon l'appel ouvrirait l'explorateur Windows pendant les tests
    calls = []
    monkeypatch.setattr("os.startfile", lambda path: calls.append(path), raising=False)
    from clipforge import config

    r = client.post("/api/open-output")
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    assert calls == [str(config.OUTPUT_DIR)]  # pas de output_dir custom -> defaut

    custom_dir = tmp_path / "mon_dossier_export"
    custom_dir.mkdir()
    client.patch("/api/settings", json={"values": {"output_dir": str(custom_dir)}})
    client.post("/api/open-output")
    assert calls[-1] == str(custom_dir)  # output_dir custom -> utilise tel quel


def test_open_output_missing_dir_400(client, monkeypatch, tmp_path):
    monkeypatch.setattr("os.startfile", lambda path: None, raising=False)
    missing_dir = tmp_path / "dossier_supprime"  # jamais cree sur disque
    client.patch("/api/settings", json={"values": {"output_dir": str(missing_dir)}})
    r = client.post("/api/open-output")
    assert r.status_code == 400
    assert "introuvable" in r.json()["detail"]
