"""Les trois evenements d'usage doivent naitre des gestes reels de l'interface."""
import importlib
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from clipforge import db as dbmod
from clipforge.api.deps import get_conn


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
    """La base que l'API vient d'initialiser, pas celle de conftest.

    Depend de `client` : sans le rechargement de config, DB_PATH pointerait
    encore sur la base du poste.
    """
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


def _profile(client):
    return client.post("/api/profiles", json={
        "name": "Scene", "cam_x": 0, "cam_y": 0, "cam_w": 480, "cam_h": 258,
        "game_x": 0, "game_y": 0, "game_w": 868, "game_h": 1080}).json()


class _SegmentsInaccessibles:
    """La base telle qu'un endpoint la voit pendant une analyse.

    Le process enfant du pipeline commite en continu ; une lecture qui tombe sur
    son verrou expire au bout des 5 s de get_db_light et leve OperationalError.
    Seule la table `segments` est rendue illisible : tout le reste de la requete
    doit continuer a fonctionner, sinon le test ne prouve rien sur le point
    precis en cause.
    """

    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql, *args):
        if "segments" in sql:
            raise sqlite3.OperationalError("database is locked")
        return self._conn.execute(sql, *args)

    def __getattr__(self, nom):
        return getattr(self._conn, nom)


@pytest.fixture()
def verrouiller_segments(client):
    """Rend `segments` illisible A PARTIR de l'appel, sans toucher au reste.

    Pose au dernier moment, et pas des l'ouverture du client : la creation du
    clip lit legitimement son candidat (clips.py:102), et cette lecture-la EST
    l'action, pas son observation. La bloquer decrirait un autre defaut que
    celui qu'on veut prouver. C'est aussi le scenario reel : le clip est prepare,
    puis une analyse demarre sur une autre VOD, puis le user exporte.
    """
    from clipforge import config
    ouvertes = []

    def verrouiller():
        conn = dbmod.get_db_light(config.DB_PATH)
        ouvertes.append(conn)
        client.app.dependency_overrides[get_conn] = lambda: _SegmentsInaccessibles(conn)

    yield verrouiller
    client.app.dependency_overrides.clear()
    for conn in ouvertes:
        conn.close()


def test_an_unreadable_candidate_never_costs_the_export_it_observes(
        client, conn, monkeypatch, verrouiller_segments):
    """L'observation lisait le rang du candidat HORS de la garde d'usage.record.

    Le user exporte pendant qu'une analyse tourne sur une autre VOD : le SELECT
    tombe sur le verrou, l'endpoint rend 500 — mais status='queued' est deja
    commite et run_in_executor n'a jamais ete atteint. Le clip reste bloque
    jusqu'au prochain redemarrage. Une observation ratee a coute l'export.
    """
    rendus = []
    monkeypatch.setattr("clipforge.api.clips.render_clip_job",
                        lambda clip_id, *a: rendus.append(clip_id))
    client.app.state.pool = ThreadPoolExecutor(max_workers=1)
    vod_id = dbmod.create_vod(conn, title="t", source_path="x.mp4")
    seg_id = _segment(conn, vod_id)
    clip = client.post("/api/clips", json={"segment_id": seg_id}).json()
    client.patch(f"/api/clips/{clip['id']}", json={"profile_id": _profile(client)["id"]})
    verrouiller_segments()

    assert client.post(f"/api/clips/{clip['id']}/render").status_code == 200

    # run_in_executor n'etait jamais atteint : c'est CA que le 500 coutait.
    assert clip["id"] in client.app.state.rendering
    for _ in range(100):
        if rendus:
            break
        time.sleep(0.05)
    assert rendus == [clip["id"]], "l'export n'a pas ete soumis au pool"
    # Le candidat EXPORTE reste l'information principale : la ligne est ecrite,
    # amputee de son contexte. La perdre entierement effacerait la mesure
    # « exporte vs ignore », que le rang venait seulement enrichir.
    evenement = conn.execute(
        "SELECT * FROM usage_events WHERE kind='clip_exported'").fetchone()
    assert evenement["segment_id"] == seg_id
    assert evenement["rank"] is None
    assert evenement["top_signal"] is None


def test_an_unreadable_candidate_never_costs_the_bound_edit_it_observes(
        client, conn, verrouiller_segments):
    """Meme schema sur la retouche des bornes : le SELECT du candidat etait
    lui aussi hors de la garde, et une lecture ratee rendait 500 sur un PATCH
    dont l'UPDATE etait deja commite."""
    vod_id = dbmod.create_vod(conn, title="t", source_path="x.mp4")
    seg_id = _segment(conn, vod_id)
    clip = client.post("/api/clips", json={"segment_id": seg_id}).json()
    verrouiller_segments()

    reponse = client.patch(f"/api/clips/{clip['id']}", json={"start_s": 96.0})

    assert reponse.status_code == 200
    assert reponse.json()["start_s"] == 96.0
    assert conn.execute("SELECT start_s FROM clips WHERE id=?",
                        (clip["id"],)).fetchone()["start_s"] == 96.0
    # Ici, contrairement a l'export, RIEN n'est ecrit : l'ecart au candidat EST
    # la mesure. Une ligne aux deux colonnes vides se lirait comme un decoupage
    # juste, ce que personne n'a constate.
    assert conn.execute(
        "SELECT COUNT(*) FROM usage_events WHERE kind='bounds_adjusted'").fetchone()[0] == 0


def test_creating_a_clip_records_the_candidate_rank_and_signal(client, conn):
    vod_id = dbmod.create_vod(conn, title="t", source_path="x.mp4")
    seg_id = _segment(conn, vod_id, rank=12, top_signal="chat")
    client.post("/api/clips", json={"segment_id": seg_id})
    row = conn.execute("SELECT * FROM usage_events WHERE kind='clip_created'").fetchone()
    assert row["rank"] == 12
    assert row["top_signal"] == "chat"
    assert row["vod_id"] == vod_id


def test_moving_a_bound_records_the_signed_shift(client, conn):
    vod_id = dbmod.create_vod(conn, title="t", source_path="x.mp4")
    seg_id = _segment(conn, vod_id)
    clip = client.post("/api/clips", json={"segment_id": seg_id}).json()
    client.patch(f"/api/clips/{clip['id']}", json={"start_s": 96.0, "end_s": 133.0})
    row = conn.execute(
        "SELECT * FROM usage_events WHERE kind='bounds_adjusted'").fetchone()
    assert row["start_shift_s"] == -4.0  # le candidat commencait 4 s trop tard
    assert row["end_shift_s"] == 3.0


def test_a_patch_that_moves_nothing_records_nothing(client, conn):
    vod_id = dbmod.create_vod(conn, title="t", source_path="x.mp4")
    seg_id = _segment(conn, vod_id)
    clip = client.post("/api/clips", json={"segment_id": seg_id}).json()
    client.patch(f"/api/clips/{clip['id']}", json={"title": "juste un titre"})
    assert conn.execute(
        "SELECT COUNT(*) FROM usage_events WHERE kind='bounds_adjusted'"
    ).fetchone()[0] == 0


def test_a_patch_at_the_current_bounds_records_nothing(client, conn):
    """ClipBoundsEditor.handleBlur envoie un PATCH a CHAQUE sortie de champ, meme
    sans frappe : entrer dans « Debut » et en ressortir suffit a rejouer les
    bornes courantes. Sans garde, tabuler trois fois triple la mesure."""
    vod_id = dbmod.create_vod(conn, title="t", source_path="x.mp4")
    seg_id = _segment(conn, vod_id)
    clip = client.post("/api/clips", json={"segment_id": seg_id}).json()
    client.patch(f"/api/clips/{clip['id']}", json={"start_s": 96.0, "end_s": 130.0})
    for _ in range(3):
        client.patch(f"/api/clips/{clip['id']}", json={"start_s": 96.0, "end_s": 130.0})
    assert conn.execute(
        "SELECT COUNT(*) FROM usage_events WHERE kind='bounds_adjusted'"
    ).fetchone()[0] == 1


def test_coming_back_onto_the_candidate_records_a_zero_shift(client, conn):
    """Revenir exactement sur le candidat est une INFORMATION : le decoupage
    etait bon. Sans ligne a 0.0, le dernier ecart connu du segment reste le
    tatonnement abandonne, et la lecture accuse un decoupage juste."""
    vod_id = dbmod.create_vod(conn, title="t", source_path="x.mp4")
    seg_id = _segment(conn, vod_id)
    clip = client.post("/api/clips", json={"segment_id": seg_id}).json()
    client.patch(f"/api/clips/{clip['id']}", json={"start_s": 96.0})
    client.patch(f"/api/clips/{clip['id']}", json={"start_s": 100.0})
    ecarts = [(r["start_shift_s"], r["end_shift_s"]) for r in conn.execute(
        "SELECT * FROM usage_events WHERE kind='bounds_adjusted' ORDER BY id")]
    assert ecarts == [(-4.0, 0.0), (0.0, 0.0)]


def test_a_second_move_is_still_measured_against_the_original_candidate(client, conn):
    """Ce qu'on mesure est l'erreur du decoupage automatique, pas le tatonnement :
    deux retouches successives donnent deux ecarts lus depuis le MEME candidat."""
    vod_id = dbmod.create_vod(conn, title="t", source_path="x.mp4")
    seg_id = _segment(conn, vod_id)
    clip = client.post("/api/clips", json={"segment_id": seg_id}).json()
    client.patch(f"/api/clips/{clip['id']}", json={"start_s": 96.0})
    client.patch(f"/api/clips/{clip['id']}", json={"start_s": 94.0})
    ecarts = [r["start_shift_s"] for r in conn.execute(
        "SELECT * FROM usage_events WHERE kind='bounds_adjusted' ORDER BY id")]
    assert ecarts == [-4.0, -6.0]  # et non [-4.0, -2.0], qui mesurerait le geste


def test_exporting_a_clip_records_the_rank_and_signal_of_its_candidate(
        client, conn, monkeypatch):
    monkeypatch.setattr("clipforge.api.clips.render_clip_job", lambda *a: None)
    client.app.state.pool = ThreadPoolExecutor(max_workers=1)  # jamais de vrai rendu
    vod_id = dbmod.create_vod(conn, title="t", source_path="x.mp4")
    seg_id = _segment(conn, vod_id, rank=2, top_signal="speech")
    clip = client.post("/api/clips", json={"segment_id": seg_id}).json()
    client.patch(f"/api/clips/{clip['id']}", json={"profile_id": _profile(client)["id"]})
    assert client.post(f"/api/clips/{clip['id']}/render").status_code == 200
    row = conn.execute("SELECT * FROM usage_events WHERE kind='clip_exported'").fetchone()
    assert row["rank"] == 2
    assert row["top_signal"] == "speech"
    assert row["vod_id"] == vod_id
    assert row["segment_id"] == seg_id


def test_a_relaunched_export_stays_a_single_exported_candidate(client, conn, monkeypatch):
    """Un candidat exporte vaut UNE ligne, quel que soit le nombre de tentatives.

    « Tout relancer » (useExportQueue.relancer) rejoue /render sur chaque clip en
    echec, et _reconcile_orphan_statuses bascule en 'error' tout clip reste
    'queued' au redemarrage. Compter les tentatives ferait dire au journal qu'un
    rang sur-represente est plebiscite alors qu'il n'a produit aucun fichier.
    """
    monkeypatch.setattr("clipforge.api.clips.render_clip_job", lambda *a: None)
    client.app.state.pool = ThreadPoolExecutor(max_workers=1)
    vod_id = dbmod.create_vod(conn, title="t", source_path="x.mp4")
    seg_id = _segment(conn, vod_id, rank=17)
    clip = client.post("/api/clips", json={"segment_id": seg_id}).json()
    client.patch(f"/api/clips/{clip['id']}", json={"profile_id": _profile(client)["id"]})
    assert client.post(f"/api/clips/{clip['id']}/render").status_code == 200
    for _ in range(2):
        # Ce que fait un redemarrage : la table des rendus en cours est perdue et
        # le clip reste sur 'error'. L'utilisateur reclique « Tout relancer ».
        client.app.state.rendering.clear()
        conn.execute("UPDATE clips SET status='error' WHERE id=?", (clip["id"],))
        conn.commit()
        assert client.post(f"/api/clips/{clip['id']}/render").status_code == 200
    assert conn.execute(
        "SELECT COUNT(*) FROM usage_events WHERE kind='clip_exported'"
    ).fetchone()[0] == 1
