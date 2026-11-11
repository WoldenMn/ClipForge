from clipforge import db as dbmod
from clipforge.pipeline.runner import build_progress_cb


def _callback(conn, clock):
    """Callback de progression d'une VOD en transcription, sur une horloge factice."""
    vod_id = dbmod.create_vod(conn, title="t", source_path="x.mp4")
    conn.execute("UPDATE vods SET status='transcribing' WHERE id=?", (vod_id,))
    conn.commit()
    return vod_id, build_progress_cb(conn, vod_id, "transcribing", clock=lambda: clock["t"])


def _row(conn, vod_id):
    return conn.execute("SELECT step_label, eta_s, progress FROM vods WHERE id=?",
                        (vod_id,)).fetchone()


def test_progress_writes_label_and_eta(conn):
    clock = {"t": 1000.0}
    vod_id, cb = _callback(conn, clock)
    cb(10.0)
    clock["t"] += 4.0
    cb(20.0)
    row = _row(conn, vod_id)
    assert row["step_label"] == "Transcription"
    assert row["progress"] == 20.0
    assert row["eta_s"] == 16.0  # 4 s pour 20 % => 16 s pour les 80 % restants


def test_progress_leaves_eta_empty_below_one_second(conn):
    """Extrapoler sur quelques millisecondes donne un temps restant absurde."""
    clock = {"t": 1000.0}
    vod_id, cb = _callback(conn, clock)
    cb(10.0)
    clock["t"] += 0.2
    cb(12.0)
    assert _row(conn, vod_id)["eta_s"] is None


def test_progress_leaves_eta_empty_on_the_first_percent(conn):
    """Un telechargement qui annonce 0,3 % apres deux secondes extrapolerait a plus
    d'une heure pour trois minutes reelles : sous le plancher, pas d'estimation."""
    clock = {"t": 1000.0}
    vod_id, cb = _callback(conn, clock)
    cb(0.1)
    clock["t"] += 2.0
    cb(0.3)
    assert _row(conn, vod_id)["eta_s"] is None


def test_progress_keeps_an_eta_when_the_percentage_stagnates(conn):
    """Un tick sans avancee ne doit pas effacer l'ETA affichee (pas de clignotement)."""
    clock = {"t": 1000.0}
    vod_id, cb = _callback(conn, clock)
    cb(10.0)
    clock["t"] += 2.0
    cb(10.0)
    assert _row(conn, vod_id)["eta_s"] == 18.0


def test_progress_ignores_a_step_that_is_no_longer_the_current_one(conn):
    clock = {"t": 1000.0}
    vod_id, cb = _callback(conn, clock)
    conn.execute("UPDATE vods SET status='segmenting' WHERE id=?", (vod_id,))
    conn.commit()
    cb(10.0)
    assert _row(conn, vod_id)["progress"] == 0.0
