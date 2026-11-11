from clipforge import db as dbmod


def test_schema_and_vod_roundtrip(conn):
    vod_id = dbmod.create_vod(conn, title="Stream test", source_path=r"C:\v\a.mp4")
    vod = dbmod.get_vod(conn, vod_id)
    assert vod["status"] == "imported"
    dbmod.set_vod_status(conn, vod_id, "transcribing", progress=42.5)
    assert dbmod.get_vod(conn, vod_id)["progress"] == 42.5


def test_settings_defaults(conn):
    assert dbmod.get_setting(conn, "weight_audio") == "1.0"
    dbmod.set_setting(conn, "ai_enabled", "1")
    assert dbmod.get_setting(conn, "ai_enabled") == "1"


def test_wal_mode(conn):
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_foreign_keys_cascade(conn):
    vod_id = dbmod.create_vod(conn, title="t", source_path="x.mp4")
    conn.execute("INSERT INTO words VALUES (?,?,?,?,?,?)", (vod_id, 0, 0.0, 0.5, "mot", 0.9))
    conn.commit()
    conn.execute("DELETE FROM vods WHERE id=?", (vod_id,))
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM words").fetchone()[0] == 0


def test_settings_survive_reopen(tmp_path):
    path = tmp_path / "re.db"
    c1 = dbmod.get_db(path)
    dbmod.set_setting(c1, "ai_enabled", "1")
    c1.close()
    c2 = dbmod.get_db(path)
    assert dbmod.get_setting(c2, "ai_enabled") == "1"
    c2.close()


def test_set_vod_error_writes_both_levels(conn):
    vod_id = dbmod.create_vod(conn, title="t", source_path="x.mp4")
    dbmod.set_vod_error(conn, vod_id, "Transcription : probleme de GPU",
                        "transcribing: cublas64_12.dll\nTraceback (most recent call last)")
    vod = dbmod.get_vod(conn, vod_id)
    assert vod["status"] == "error"
    assert vod["error"] == "Transcription : probleme de GPU"
    assert "Traceback" in vod["error_detail"]
    assert vod["updated_at"]


def test_set_vod_status_clears_the_previous_error_and_eta(conn):
    vod_id = dbmod.create_vod(conn, title="t", source_path="x.mp4")
    dbmod.set_vod_error(conn, vod_id, "court", "technique")
    conn.execute("UPDATE vods SET eta_s=42.0 WHERE id=?", (vod_id,))
    conn.commit()
    dbmod.set_vod_status(conn, vod_id, "transcribing", step_label="Transcription")
    vod = dbmod.get_vod(conn, vod_id)
    assert vod["error"] is None and vod["error_detail"] is None
    assert vod["step_label"] == "Transcription" and vod["eta_s"] is None


def _profil(conn, nom: str) -> int:
    cur = conn.execute(
        "INSERT INTO scene_profiles (name, cam_x, cam_y, cam_w, cam_h, "
        "game_x, game_y, game_w, game_h) VALUES (?,0,0,640,340,640,0,868,1080)", (nom,))
    conn.commit()
    return cur.lastrowid


def test_auto_profile_is_the_sole_profile_without_a_name(conn):
    assert dbmod.auto_profile_id(conn) is None
    seul = _profil(conn, "plateau")
    assert dbmod.auto_profile_id(conn) == seul
    _profil(conn, "obs")
    assert dbmod.auto_profile_id(conn) is None


def test_auto_profile_follows_the_named_profile_and_never_another(conn):
    _profil(conn, "plateau")
    obs = _profil(conn, "obs")
    dbmod.set_setting(conn, "auto_profile", "obs")
    assert dbmod.auto_profile_id(conn) == obs
    conn.execute("DELETE FROM scene_profiles WHERE id=?", (obs,))
    conn.commit()
    assert dbmod.auto_profile_id(conn) is None


def test_a_vod_framed_by_its_own_profile_ignores_the_setting(conn):
    vod_obs = dbmod.create_vod(conn, title="obs", source_path="a.mp4")
    vod_twitch = dbmod.create_vod(conn, title="twitch", source_path="b.mp4")
    plateau = _profil(conn, "plateau")
    obs = _profil(conn, "obs 1440p")
    dbmod.set_setting(conn, "auto_profile", "plateau")
    conn.execute("UPDATE vods SET profile_id=? WHERE id=?", (obs, vod_obs))
    conn.commit()
    assert dbmod.clip_profile_id(conn, vod_obs) == obs
    assert dbmod.clip_profile_id(conn, vod_twitch) == plateau


def test_a_vod_whose_profile_is_deleted_follows_the_setting_again(conn):
    vod = dbmod.create_vod(conn, title="obs", source_path="a.mp4")
    plateau = _profil(conn, "plateau")
    obs = _profil(conn, "obs 1440p")
    dbmod.set_setting(conn, "auto_profile", "plateau")
    conn.execute("UPDATE vods SET profile_id=? WHERE id=?", (obs, vod))
    conn.execute("DELETE FROM scene_profiles WHERE id=?", (obs,))
    conn.commit()
    assert dbmod.get_vod(conn, vod)["profile_id"] is None
    assert dbmod.clip_profile_id(conn, vod) == plateau
