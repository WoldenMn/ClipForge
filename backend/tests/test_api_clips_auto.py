"""Garder/rejeter un clip auto, et le statut (jamais la valeur) de la cle IA."""
import importlib
import sqlite3
import threading

import pytest
from fastapi.testclient import TestClient

from clipforge import db as dbmod
from clipforge.api.deps import get_conn

SENTINELLE = "sk-ant-tres-secret-42"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CLIPFORGE_DATA", str(tmp_path))
    from clipforge import config
    importlib.reload(config)
    from clipforge import main as mainmod
    importlib.reload(mainmod)
    with TestClient(mainmod.app, base_url="http://127.0.0.1:8765") as c:
        yield c


@pytest.fixture()
def conn(client):
    from clipforge import config
    c = dbmod.get_db(config.DB_PATH)
    yield c
    c.close()


def _segment(conn, vod_id, rank=1, top_signal="audio"):
    conn.execute(
        "INSERT INTO segments (vod_id, start_s, end_s, score, rank, top_signal) "
        "VALUES (?,?,?,?,?,?)", (vod_id, 100.0, 130.0, 0.9, rank, top_signal))
    conn.commit()
    return conn.execute("SELECT id FROM segments ORDER BY id DESC").fetchone()["id"]


def _auto_clip(client, conn, seg_id):
    """Un clip cree par l'API, puis bascule 'auto' — la fabrication reelle vit
    dans autoclip.py (pipeline), hors de portee d'un test d'API."""
    clip_id = client.post("/api/clips", json={"segment_id": seg_id}).json()["id"]
    conn.execute("UPDATE clips SET origin='auto' WHERE id=?", (clip_id,))
    conn.commit()
    return clip_id


def test_keep_records_the_candidates_rank_and_signal_once(client, conn):
    vod_id = dbmod.create_vod(conn, title="t", source_path="x.mp4")
    seg_id = _segment(conn, vod_id, rank=4, top_signal="chat")
    clip_id = _auto_clip(client, conn, seg_id)

    r = client.post(f"/api/clips/{clip_id}/keep")

    assert r.status_code == 200
    row = conn.execute("SELECT * FROM usage_events WHERE kind='auto_clip_kept'").fetchone()
    assert row["vod_id"] == vod_id
    assert row["segment_id"] == seg_id
    assert row["rank"] == 4
    assert row["top_signal"] == "chat"


def test_a_second_keep_writes_no_second_event(client, conn):
    vod_id = dbmod.create_vod(conn, title="t", source_path="x.mp4")
    seg_id = _segment(conn, vod_id)
    clip_id = _auto_clip(client, conn, seg_id)

    assert client.post(f"/api/clips/{clip_id}/keep").status_code == 200
    assert client.post(f"/api/clips/{clip_id}/keep").status_code == 200

    assert conn.execute(
        "SELECT COUNT(*) FROM usage_events WHERE kind='auto_clip_kept'"
    ).fetchone()[0] == 1


def test_two_concurrent_keeps_never_double_the_event(client, conn):
    """Deux clics rapides sur « Garder » : deux requetes HTTP dans deux threads,
    chacune ouvre sa propre connexion (get_conn), donc un check-then-insert
    cote application laisse une fenetre ou les deux passent le SELECT avant
    que l'une des deux n'ecrive. Un index unique partiel (ux_usage_kept)
    tranche desormais au niveau de SQLite : ce test reproduit la course pour
    de vrai plutot que de la simuler."""
    vod_id = dbmod.create_vod(conn, title="t", source_path="x.mp4")
    seg_id = _segment(conn, vod_id)
    clip_id = _auto_clip(client, conn, seg_id)

    barrier = threading.Barrier(2)
    results = []

    def _keep():
        barrier.wait()
        results.append(client.post(f"/api/clips/{clip_id}/keep").status_code)

    threads = [threading.Thread(target=_keep) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert results == [200, 200]
    assert conn.execute(
        "SELECT COUNT(*) FROM usage_events WHERE kind='auto_clip_kept'"
    ).fetchone()[0] == 1


def test_keep_with_a_null_segment_still_records_once(client, conn):
    """Apres une re-analyse, segment_id d'un clip auto peut passer a NULL
    (cascade ON DELETE SET NULL) : le keep ne doit ni lever ni se taire."""
    vod_id = dbmod.create_vod(conn, title="t", source_path="x.mp4")
    seg_id = _segment(conn, vod_id)
    clip_id = _auto_clip(client, conn, seg_id)
    conn.execute("UPDATE clips SET segment_id=NULL WHERE id=?", (clip_id,))
    conn.commit()

    r = client.post(f"/api/clips/{clip_id}/keep")

    assert r.status_code == 200
    row = conn.execute("SELECT * FROM usage_events WHERE kind='auto_clip_kept'").fetchone()
    assert row["segment_id"] is None
    assert row["rank"] is None
    assert row["top_signal"] is None


def test_a_second_null_segment_keep_writes_a_second_event(client, conn):
    """Ecart assume et documente (usage.record_auto_kept) : sans segment_id,
    l'index unique ne couvre pas ce cas (WHERE segment_id IS NOT NULL), donc
    l'idempotence n'y est plus garantie. Ce test rend le trou EXPLICITE — s'il
    se met a echouer, l'idempotence s'est refermee sans que ce soit documente
    ailleurs, ce qui merite d'etre vu plutot que de passer inapercu en vert."""
    vod_id = dbmod.create_vod(conn, title="t", source_path="x.mp4")
    seg_id = _segment(conn, vod_id)
    clip_id = _auto_clip(client, conn, seg_id)
    conn.execute("UPDATE clips SET segment_id=NULL WHERE id=?", (clip_id,))
    conn.commit()

    assert client.post(f"/api/clips/{clip_id}/keep").status_code == 200
    assert client.post(f"/api/clips/{clip_id}/keep").status_code == 200

    assert conn.execute(
        "SELECT COUNT(*) FROM usage_events WHERE kind='auto_clip_kept'"
    ).fetchone()[0] == 2


def test_keeping_a_user_clip_is_refused_409(client, conn):
    vod_id = dbmod.create_vod(conn, title="t", source_path="x.mp4")
    seg_id = _segment(conn, vod_id)
    clip_id = client.post("/api/clips", json={"segment_id": seg_id}).json()["id"]

    r = client.post(f"/api/clips/{clip_id}/keep")

    assert r.status_code == 409
    assert conn.execute(
        "SELECT COUNT(*) FROM usage_events WHERE kind='auto_clip_kept'"
    ).fetchone()[0] == 0


def test_keeping_an_unknown_clip_is_404(client):
    assert client.post("/api/clips/999/keep").status_code == 404


def test_deleting_an_auto_clip_records_the_rejection_before_removal(client, conn):
    vod_id = dbmod.create_vod(conn, title="t", source_path="x.mp4")
    seg_id = _segment(conn, vod_id, rank=2, top_signal="speech")
    clip_id = _auto_clip(client, conn, seg_id)

    r = client.delete(f"/api/clips/{clip_id}")

    assert r.status_code == 200
    assert conn.execute("SELECT * FROM clips WHERE id=?", (clip_id,)).fetchone() is None
    row = conn.execute("SELECT * FROM usage_events WHERE kind='auto_clip_rejected'").fetchone()
    assert row["vod_id"] == vod_id
    assert row["segment_id"] == seg_id
    assert row["rank"] == 2
    assert row["top_signal"] == "speech"


class _DeleteClipsLocked:
    """Le DELETE echoue (base verrouillee) ; tout le reste passe normalement."""

    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql, *args):
        if sql.strip().upper().startswith("DELETE FROM CLIPS"):
            raise sqlite3.OperationalError("database is locked")
        return self._conn.execute(sql, *args)

    def __getattr__(self, nom):
        return getattr(self._conn, nom)


def test_a_failed_delete_never_leaves_a_phantom_rejection(client, conn):
    """CRITIQUE corrige : ecrire l'evenement AVANT le DELETE laissait un rejet
    durable pour un clip toujours vivant des que le DELETE echouait ensuite
    (base verrouillee par le process de rendu, scenario realiste). Desormais
    le DELETE part en premier : s'il leve, rien n'a encore ete consigne."""
    vod_id = dbmod.create_vod(conn, title="t", source_path="x.mp4")
    seg_id = _segment(conn, vod_id)
    clip_id = _auto_clip(client, conn, seg_id)

    from clipforge import config
    verrou = dbmod.get_db_light(config.DB_PATH)
    client.app.dependency_overrides[get_conn] = lambda: _DeleteClipsLocked(verrou)
    try:
        with pytest.raises(sqlite3.OperationalError):
            client.delete(f"/api/clips/{clip_id}")
    finally:
        client.app.dependency_overrides.clear()
        verrou.close()

    assert conn.execute("SELECT 1 FROM clips WHERE id=?", (clip_id,)).fetchone() is not None
    assert conn.execute(
        "SELECT COUNT(*) FROM usage_events WHERE kind='auto_clip_rejected'"
    ).fetchone()[0] == 0


class _VanishBeforeDelete:
    """Simule une seconde requete qui supprime la ligne entre le SELECT et le
    DELETE de la premiere : le DELETE de la premiere ne touche alors 0 ligne."""

    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql, *args):
        if sql.strip().upper().startswith("DELETE FROM CLIPS"):
            from clipforge import config
            autre = dbmod.get_db_light(config.DB_PATH)
            autre.execute(sql, *args)
            autre.commit()
            autre.close()
        return self._conn.execute(sql, *args)

    def __getattr__(self, nom):
        return getattr(self._conn, nom)


def test_a_row_vanished_between_select_and_delete_is_404_with_no_event(client, conn):
    """Course fermee par `cursor.rowcount == 1` : si le DELETE ne touche
    aucune ligne (deja supprimee par une autre requete entre-temps), 404 et
    AUCUN evenement — pas un rejet fantome pour un clip deja parti."""
    vod_id = dbmod.create_vod(conn, title="t", source_path="x.mp4")
    seg_id = _segment(conn, vod_id)
    clip_id = _auto_clip(client, conn, seg_id)

    from clipforge import config
    ouvert = dbmod.get_db_light(config.DB_PATH)
    client.app.dependency_overrides[get_conn] = lambda: _VanishBeforeDelete(ouvert)
    try:
        r = client.delete(f"/api/clips/{clip_id}")
    finally:
        client.app.dependency_overrides.clear()
        ouvert.close()

    assert r.status_code == 404
    assert conn.execute(
        "SELECT COUNT(*) FROM usage_events WHERE kind='auto_clip_rejected'"
    ).fetchone()[0] == 0


def test_deleting_an_auto_clip_twice_records_one_event_and_404s_the_second(client, conn):
    vod_id = dbmod.create_vod(conn, title="t", source_path="x.mp4")
    seg_id = _segment(conn, vod_id)
    clip_id = _auto_clip(client, conn, seg_id)

    first = client.delete(f"/api/clips/{clip_id}")
    second = client.delete(f"/api/clips/{clip_id}")

    assert first.status_code == 200
    assert second.status_code == 404
    assert conn.execute(
        "SELECT COUNT(*) FROM usage_events WHERE kind='auto_clip_rejected'"
    ).fetchone()[0] == 1


def test_deleting_a_user_clip_records_nothing(client, conn):
    vod_id = dbmod.create_vod(conn, title="t", source_path="x.mp4")
    seg_id = _segment(conn, vod_id)
    clip_id = client.post("/api/clips", json={"segment_id": seg_id}).json()["id"]

    r = client.delete(f"/api/clips/{clip_id}")

    assert r.status_code == 200
    assert conn.execute("SELECT * FROM clips WHERE id=?", (clip_id,)).fetchone() is None
    assert conn.execute(
        "SELECT COUNT(*) FROM usage_events WHERE kind LIKE 'auto_clip_%'"
    ).fetchone()[0] == 0


def test_deleting_an_unknown_clip_is_404(client):
    assert client.delete("/api/clips/999").status_code == 404


def test_env_reports_key_absent_by_default(client, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert client.get("/api/settings/env").json() == {"anthropic_key": "absente"}


def test_env_reports_key_present_when_set(client, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", SENTINELLE)
    assert client.get("/api/settings/env").json() == {"anthropic_key": "presente"}


def test_env_never_leaks_the_key_value(client, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", SENTINELLE)
    r = client.get("/api/settings/env")
    assert SENTINELLE not in r.text


def test_env_endpoint_never_probes_the_machine(client, monkeypatch):
    """La lourdeur EST le bug corrige : machine_state() sonde ffmpeg, ffprobe
    et le GPU par subprocess (~196 ms mesures, secondes au pire) pour lire une
    seule variable d'environnement. L'endpoint doit passer par le helper pur,
    jamais par machine_state()."""
    from clipforge import diagnostic

    def _ne_devrait_pas_etre_appelee():
        raise AssertionError("machine_state() ne doit pas etre appelee par /api/settings/env")

    monkeypatch.setattr(diagnostic, "machine_state", _ne_devrait_pas_etre_appelee)
    assert client.get("/api/settings/env").status_code == 200


@pytest.mark.parametrize("statut", ["queued", "rendering"])
def test_deleting_a_clip_mid_render_is_refused_409(client, conn, statut):
    """Le garde UI (bouton desactive) a une fenetre de 2 s entre deux polls :
    un second onglet ou un appel direct peut supprimer la ligne pendant que
    render_clip_job ecrit — fichier orphelin, UPDATE silencieux sur 0 ligne.
    Le refus vit donc cote serveur, sur le statut."""
    vod_id = dbmod.create_vod(conn, title="t", source_path="x.mp4")
    seg_id = _segment(conn, vod_id)
    clip_id = _auto_clip(client, conn, seg_id)
    conn.execute("UPDATE clips SET status=? WHERE id=?", (statut, clip_id))
    conn.commit()

    r = client.delete(f"/api/clips/{clip_id}")

    assert r.status_code == 409
    assert "Montage en cours" in r.json()["detail"]
    assert conn.execute("SELECT COUNT(*) FROM clips WHERE id=?",
                        (clip_id,)).fetchone()[0] == 1
    assert conn.execute(
        "SELECT COUNT(*) FROM usage_events WHERE kind='auto_clip_rejected'"
    ).fetchone()[0] == 0


def test_the_render_guard_covers_user_clips_too(client, conn):
    """Le garde protege le RENDU, pas l'origine : un clip de l'utilisateur en
    cours de montage ne doit pas etre supprimable non plus."""
    vod_id = dbmod.create_vod(conn, title="t", source_path="x.mp4")
    seg_id = _segment(conn, vod_id)
    clip_id = client.post("/api/clips", json={"segment_id": seg_id}).json()["id"]
    conn.execute("UPDATE clips SET status='rendering' WHERE id=?", (clip_id,))
    conn.commit()

    assert client.delete(f"/api/clips/{clip_id}").status_code == 409


def test_a_finished_clip_stays_deletable(client, conn):
    vod_id = dbmod.create_vod(conn, title="t", source_path="x.mp4")
    seg_id = _segment(conn, vod_id)
    clip_id = _auto_clip(client, conn, seg_id)
    conn.execute("UPDATE clips SET status='done' WHERE id=?", (clip_id,))
    conn.commit()

    assert client.delete(f"/api/clips/{clip_id}").status_code == 200
