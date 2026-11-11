"""Evolution du schema : chaque version doit s'appliquer sans perdre de donnee."""
import sqlite3

from clipforge import db as dbmod


def test_migration_adds_v2_columns(tmp_path):
    path = tmp_path / "v1.db"
    # base V1 : segments sans les colonnes V2
    c = sqlite3.connect(path)
    c.executescript("""
        CREATE TABLE segments (id INTEGER PRIMARY KEY AUTOINCREMENT, vod_id INTEGER,
          start_s REAL, end_s REAL, score REAL, ai_score REAL, ai_title TEXT,
          ai_hashtags TEXT, rank INTEGER, top_signal TEXT);
        INSERT INTO segments (vod_id, start_s, end_s, score) VALUES (1, 0.0, 30.0, 0.5);
    """)
    c.commit()
    c.close()
    conn = dbmod.get_db(path)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(segments)")}
    assert "signals_count" in cols and "hook_text" in cols
    # la donnee V1 survit
    assert conn.execute("SELECT COUNT(*) FROM segments").fetchone()[0] == 1
    assert conn.execute("SELECT version FROM schema_version").fetchone()[0] >= 2
    conn.close()


def test_migration_is_idempotent(conn):
    v1 = conn.execute("SELECT version FROM schema_version").fetchone()[0]
    dbmod.migrate(conn)
    dbmod.migrate(conn)
    assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == v1


def test_migration_v3_backfills_words_complete_for_ready_vods(tmp_path):
    path = tmp_path / "v2.db"
    # base V2 simulee : vods/words sans words_complete, une vod ready avec ses mots
    c = sqlite3.connect(path)
    c.executescript("""
        CREATE TABLE vods (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          title TEXT NOT NULL, source_path TEXT NOT NULL DEFAULT '',
          twitch_url TEXT, twitch_vod_id TEXT,
          duration_s REAL, width INTEGER, height INTEGER, fps REAL,
          status TEXT NOT NULL DEFAULT 'imported', progress REAL NOT NULL DEFAULT 0,
          error TEXT, ai_note TEXT,
          created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
        );
        CREATE TABLE words (
          vod_id INTEGER NOT NULL, idx INTEGER NOT NULL,
          start_s REAL NOT NULL, end_s REAL NOT NULL,
          text TEXT NOT NULL, probability REAL NOT NULL,
          PRIMARY KEY (vod_id, idx)
        );
        CREATE TABLE schema_version (version INTEGER NOT NULL);
        INSERT INTO vods (id, title, status) VALUES (1, 't', 'ready');
        INSERT INTO words VALUES (1, 0, 0.0, 0.5, 'mot', 0.9);
        INSERT INTO schema_version VALUES (2);
    """)
    c.commit()
    c.close()
    conn = dbmod.get_db(path)
    row = conn.execute("SELECT words_complete FROM vods WHERE id=1").fetchone()
    assert row["words_complete"] == 1
    conn.close()


V3_BASE = """
    CREATE TABLE vods (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      title TEXT NOT NULL, source_path TEXT NOT NULL DEFAULT '',
      twitch_url TEXT, twitch_vod_id TEXT,
      duration_s REAL, width INTEGER, height INTEGER, fps REAL,
      status TEXT NOT NULL DEFAULT 'imported', progress REAL NOT NULL DEFAULT 0,
      error TEXT, ai_note TEXT,
      created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
      words_complete INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE words (
      vod_id INTEGER NOT NULL, idx INTEGER NOT NULL,
      start_s REAL NOT NULL, end_s REAL NOT NULL,
      text TEXT NOT NULL, probability REAL NOT NULL,
      PRIMARY KEY (vod_id, idx)
    );
    CREATE TABLE segments (
      id INTEGER PRIMARY KEY AUTOINCREMENT, vod_id INTEGER,
      start_s REAL, end_s REAL, score REAL, ai_score REAL, ai_title TEXT,
      ai_hashtags TEXT, rank INTEGER, top_signal TEXT,
      signals_count INTEGER, hook_text TEXT
    );
    CREATE TABLE schema_version (version INTEGER NOT NULL);
    INSERT INTO vods (id, title, source_path, duration_s, status, progress, words_complete)
      VALUES (1, 'Stream du 2 aout', 'C:\\v\\stream.mp4', 9660.0, 'ready', 100.0, 1);
    INSERT INTO words VALUES (1, 0, 0.0, 0.5, 'salut', 0.9), (1, 1, 0.5, 1.0, 'tout', 0.9);
    INSERT INTO segments (vod_id, start_s, end_s, score, rank, signals_count, hook_text)
      VALUES (1, 120.0, 150.0, 0.8, 1, 3, 'salut tout');
    INSERT INTO schema_version VALUES (3);
"""


def test_migration_v4_adds_error_and_progress_columns_without_losing_data(tmp_path):
    path = tmp_path / "v3.db"
    c = sqlite3.connect(path)
    c.executescript(V3_BASE)
    c.commit()
    c.close()

    conn = dbmod.get_db(path)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(vods)")}
    assert {"error_detail", "step_label", "eta_s", "updated_at"} <= cols
    vod = conn.execute("SELECT * FROM vods WHERE id=1").fetchone()
    assert vod["title"] == "Stream du 2 aout"
    assert vod["duration_s"] == 9660.0
    assert vod["status"] == "ready" and vod["words_complete"] == 1
    assert conn.execute("SELECT COUNT(*) FROM words").fetchone()[0] == 2
    assert conn.execute("SELECT hook_text FROM segments").fetchone()[0] == "salut tout"
    assert (conn.execute("SELECT version FROM schema_version").fetchone()[0]
            == dbmod.CURRENT_SCHEMA_VERSION)
    conn.close()


def test_migration_v4_is_idempotent_on_an_already_migrated_base(tmp_path):
    path = tmp_path / "v3bis.db"
    c = sqlite3.connect(path)
    c.executescript(V3_BASE)
    c.commit()
    c.close()

    dbmod.get_db(path).close()
    conn = dbmod.get_db(path)  # deuxieme ouverture : les ALTER TABLE sont deja passes
    assert (conn.execute("SELECT version FROM schema_version").fetchone()[0]
            == dbmod.CURRENT_SCHEMA_VERSION)
    assert conn.execute("SELECT COUNT(*) FROM vods").fetchone()[0] == 1
    conn.close()


LEGACY_ERROR = (
    "downloading: Telechargement echoue : ERROR: ffmpeg exited with code 1\n"
    "DownloadError: ERROR: ffmpeg exited with code 1\n\n"
    "Traceback (most recent call last):\n"
    '  File "runner.py", line 38, in run_steps\n'
)


def _base_v4(tmp_path, name, rows):
    """Base complete ramenee a la version 4 : la migration de donnees reste a faire."""
    path = tmp_path / name
    conn = dbmod.get_db(path)
    for title, error, detail in rows:
        vod_id = dbmod.create_vod(conn, title=title, source_path="x.mp4")
        conn.execute("UPDATE vods SET status='error', error=?, error_detail=? WHERE id=?",
                     (error, detail, vod_id))
    conn.execute("DELETE FROM schema_version")
    conn.execute("INSERT INTO schema_version VALUES (4)")
    conn.commit()
    conn.close()
    return path


def test_migration_v5_moves_the_trace_of_a_legacy_error_into_the_detail(tmp_path):
    path = _base_v4(tmp_path, "legacy.db", [("heritee", LEGACY_ERROR, None)])
    conn = dbmod.get_db(path)
    vod = conn.execute("SELECT * FROM vods WHERE title='heritee'").fetchone()
    assert vod["error"] == (
        "downloading: Telechargement echoue : ERROR: ffmpeg exited with code 1")
    assert "\n" not in vod["error"]
    assert vod["error_detail"].startswith("DownloadError")
    assert "Traceback" in vod["error_detail"]
    assert (conn.execute("SELECT version FROM schema_version").fetchone()[0]
            == dbmod.CURRENT_SCHEMA_VERSION)
    conn.close()


def test_migration_v5_leaves_errors_already_at_the_right_shape_alone(tmp_path):
    path = _base_v4(tmp_path, "shaped.db", [
        ("deja scindee", "Telechargement : Twitch a refuse le telechargement.",
         "downloading: HTTP 403\nTraceback"),
        ("sans trace", "Transcription : memoire insuffisante.", None),
        ("sans erreur", None, None),
        ("saut en tete", "\nmessage apres un saut", None),
        ("saut final", "Decoupage : fichier introuvable.\n", None),
    ])
    conn = dbmod.get_db(path)
    rows = {r["title"]: r for r in conn.execute("SELECT * FROM vods")}
    assert rows["deja scindee"]["error_detail"] == "downloading: HTTP 403\nTraceback"
    assert rows["deja scindee"]["error"].startswith("Telechargement :")
    assert rows["sans trace"]["error"] == "Transcription : memoire insuffisante."
    assert rows["sans trace"]["error_detail"] is None
    assert rows["sans erreur"]["error"] is None and rows["sans erreur"]["error_detail"] is None
    # message vide interdit : un saut de ligne en tete ne doit rien deplacer
    assert rows["saut en tete"]["error"] == "\nmessage apres un saut"
    assert rows["saut en tete"]["error_detail"] is None
    # detail vide interdit : le bouton « detail » n'ouvrirait rien
    assert rows["saut final"]["error"] == "Decoupage : fichier introuvable.\n"
    assert rows["saut final"]["error_detail"] is None
    conn.close()


def test_migration_v6_adds_usage_events_and_keeps_data(tmp_path):
    """Une base v5 reelle doit gagner la table sans rien perdre."""
    path = tmp_path / "v5.db"
    conn = dbmod.get_db(path)
    vod_id = dbmod.create_vod(conn, title="avant", source_path="a.mp4")
    conn.execute("DELETE FROM schema_version")
    conn.execute("INSERT INTO schema_version VALUES (5)")
    conn.execute("DROP TABLE usage_events")
    conn.commit()
    conn.close()

    conn = dbmod.get_db_light(path)
    dbmod.migrate(conn)
    assert (conn.execute("SELECT version FROM schema_version").fetchone()["version"]
            == dbmod.CURRENT_SCHEMA_VERSION)
    vod = conn.execute("SELECT title FROM vods WHERE id=?", (vod_id,)).fetchone()
    assert vod["title"] == "avant"
    conn.execute("INSERT INTO usage_events (kind) VALUES ('clip_created')")
    conn.commit()
    dbmod.migrate(conn)  # idempotence : la table et son contenu survivent
    assert conn.execute("SELECT COUNT(*) FROM usage_events").fetchone()[0] == 1
    conn.close()


def test_migration_v5_is_idempotent_when_replayed(tmp_path):
    path = _base_v4(tmp_path, "replay.db", [("heritee", LEGACY_ERROR, None)])
    conn = dbmod.get_db(path)
    apres = dict(conn.execute("SELECT error, error_detail FROM vods").fetchone())
    for _ in range(2):  # rejouer la migration sur une base deja migree
        conn.execute("DELETE FROM schema_version")
        conn.execute("INSERT INTO schema_version VALUES (4)")
        conn.commit()
        dbmod.migrate(conn)
    assert dict(conn.execute("SELECT error, error_detail FROM vods").fetchone()) == apres
    assert (conn.execute("SELECT version FROM schema_version").fetchone()[0]
            == dbmod.CURRENT_SCHEMA_VERSION)
    conn.close()


def test_migration_v7_adds_origin_and_hook_and_keeps_data(tmp_path):
    path = tmp_path / "v6.db"
    conn = dbmod.get_db(path)
    vod_id = dbmod.create_vod(conn, title="avant", source_path="a.mp4")
    conn.execute("INSERT INTO clips (vod_id, start_s, end_s) VALUES (?, 1.0, 2.0)", (vod_id,))
    conn.execute("DELETE FROM schema_version")
    conn.execute("INSERT INTO schema_version VALUES (6)")
    conn.commit(); conn.close()

    conn = dbmod.get_db(path)
    dbmod.migrate(conn)
    row = conn.execute("SELECT origin, hook, emoji, start_s FROM clips").fetchone()
    assert row["origin"] == "user" and row["hook"] is None and row["emoji"] is None
    assert row["start_s"] == 1.0
    dbmod.migrate(conn)  # idempotence
    assert (conn.execute("SELECT version FROM schema_version").fetchone()["version"]
            == dbmod.CURRENT_SCHEMA_VERSION)


def test_migration_v8_gives_each_vod_a_profile_slot_and_keeps_data(tmp_path):
    """Une base v7 gagne vods.profile_id a NULL : chaque VOD suit le reglage comme avant."""
    path = tmp_path / "v7.db"
    c = sqlite3.connect(path)
    c.executescript(dbmod.SCHEMA)
    c.execute("INSERT INTO schema_version VALUES (7)")
    c.execute("INSERT INTO vods (title, source_path) VALUES ('avant', 'a.mp4')")
    c.commit()
    c.close()

    conn = dbmod.get_db(path)
    row = conn.execute("SELECT title, profile_id FROM vods").fetchone()
    assert row["title"] == "avant" and row["profile_id"] is None
    dbmod.migrate(conn)
    assert (conn.execute("SELECT version FROM schema_version").fetchone()["version"]
            == dbmod.CURRENT_SCHEMA_VERSION)
    conn.close()


def test_new_settings_are_seeded_with_sane_defaults(conn):
    assert dbmod.get_setting(conn, "channel_handle") == ""
    assert dbmod.get_setting(conn, "captions_emojis") == "1"
    assert dbmod.get_setting(conn, "auto_clip_count") == "5"
    assert dbmod.get_setting(conn, "montage") == "1"
    assert dbmod.get_setting(conn, "cut_style") == "standard"
