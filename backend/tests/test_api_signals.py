import pytest
from fastapi.testclient import TestClient

from clipforge.api.segments import MAX_SIGNAL_POINTS


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


def _vod_with_signals(client, tmp_path, duration_s, mesures):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    vod_id = client.post("/api/vods/import-local", json={"path": str(video)}).json()["id"]
    from clipforge import config
    from clipforge import db as dbmod
    conn = dbmod.get_db(config.DB_PATH)
    conn.execute("UPDATE vods SET duration_s=? WHERE id=?", (duration_s, vod_id))
    conn.executemany("INSERT INTO signals VALUES (?,?,?,?)",
                     [(vod_id, kind, t, s) for kind, t, s in mesures])
    conn.commit()
    conn.close()
    return vod_id


def test_signals_average_inside_each_slice(client, tmp_path):
    # 4 tranches de 25 s : la premiere contient deux mesures a moyenner
    vod_id = _vod_with_signals(client, tmp_path, 100.0, [
        ("audio", 0.0, 0.2), ("audio", 10.0, 0.6),
        ("audio", 30.0, 1.0), ("audio", 80.0, 0.5),
    ])
    body = client.get(f"/api/vods/{vod_id}/signals", params={"step": 25.0}).json()
    assert body["duration_s"] == 100.0
    assert body["step_s"] == 25.0
    assert len(body["series"]) == 1
    scores = body["series"][0]["scores"]
    assert body["series"][0]["kind"] == "audio"
    assert scores == [pytest.approx(0.4), pytest.approx(1.0), None, pytest.approx(0.5)]


def test_signals_never_exceed_the_point_ceiling(client, tmp_path):
    vod_id = _vod_with_signals(client, tmp_path, 9600.0,
                               [("audio", float(t), 0.5) for t in range(0, 9600, 5)])
    body = client.get(f"/api/vods/{vod_id}/signals").json()
    scores = body["series"][0]["scores"]
    assert len(scores) == MAX_SIGNAL_POINTS
    assert body["step_s"] == pytest.approx(9600.0 / MAX_SIGNAL_POINTS)
    assert all(v == pytest.approx(0.5) for v in scores)


def test_signals_ceiling_wins_over_a_step_that_is_too_fine(client, tmp_path):
    vod_id = _vod_with_signals(client, tmp_path, 9600.0, [("audio", 0.0, 1.0)])
    body = client.get(f"/api/vods/{vod_id}/signals", params={"step": 0.5}).json()
    assert len(body["series"][0]["scores"]) == MAX_SIGNAL_POINTS


def test_signals_last_measure_stays_inside_the_last_slice(client, tmp_path):
    # t == duree exacte : la tranche calculee deborde d'un cran, elle doit etre ramenee
    vod_id = _vod_with_signals(client, tmp_path, 100.0,
                               [("audio", 100.0, 0.9), ("audio", 0.0, 0.1)])
    scores = client.get(f"/api/vods/{vod_id}/signals",
                        params={"step": 50.0}).json()["series"][0]["scores"]
    assert len(scores) == 2
    assert scores[1] == pytest.approx(0.9)


def test_signals_filter_on_one_kind(client, tmp_path):
    vod_id = _vod_with_signals(client, tmp_path, 100.0,
                               [("audio", 10.0, 0.4), ("chat", 10.0, 0.8)])
    body = client.get(f"/api/vods/{vod_id}/signals", params={"kind": "chat"}).json()
    assert [s["kind"] for s in body["series"]] == ["chat"]
    both = client.get(f"/api/vods/{vod_id}/signals").json()
    assert [s["kind"] for s in both["series"]] == ["audio", "chat"]


def test_signals_without_duration_or_measure_stay_empty(client, tmp_path):
    vod_id = _vod_with_signals(client, tmp_path, None, [])
    body = client.get(f"/api/vods/{vod_id}/signals").json()
    assert body == {"duration_s": 0.0, "step_s": 0.0, "series": []}


def test_signals_fall_back_on_the_last_measure_when_duration_is_unknown(client, tmp_path):
    vod_id = _vod_with_signals(client, tmp_path, None, [("audio", 60.0, 0.7)])
    body = client.get(f"/api/vods/{vod_id}/signals").json()
    assert body["duration_s"] == 60.0
    assert any(v == pytest.approx(0.7) for v in body["series"][0]["scores"])


def test_signals_unknown_vod_404(client):
    assert client.get("/api/vods/99999/signals").status_code == 404


def test_signals_reject_a_negative_step(client, tmp_path):
    vod_id = _vod_with_signals(client, tmp_path, 100.0, [("audio", 1.0, 0.5)])
    assert client.get(f"/api/vods/{vod_id}/signals",
                      params={"step": -1}).status_code == 422
