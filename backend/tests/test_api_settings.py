import pytest
from fastapi.testclient import TestClient

from clipforge.render.montage.edl import CUT_THRESHOLDS


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


def test_patch_settings_auto_clip_count_rejects_non_integer_422(client):
    r = client.patch("/api/settings", json={"values": {"auto_clip_count": "abc"}})
    assert r.status_code == 422


def test_patch_settings_auto_clip_count_rejects_out_of_range_422(client):
    r = client.patch("/api/settings", json={"values": {"auto_clip_count": "25"}})
    assert r.status_code == 422


def test_patch_settings_captions_emojis_rejects_invalid_value_422(client):
    r = client.patch("/api/settings", json={"values": {"captions_emojis": "2"}})
    assert r.status_code == 422


def test_patch_settings_channel_handle_rejects_too_long_422(client):
    r = client.patch("/api/settings", json={"values": {"channel_handle": "x" * 60}})
    assert r.status_code == 422


def test_patch_settings_channel_handle_accepts_valid_values(client):
    r = client.patch("/api/settings", json={"values": {"channel_handle": "twitch.tv/pseudo"}})
    assert r.status_code == 200
    assert r.json()["channel_handle"] == "twitch.tv/pseudo"
    r = client.patch("/api/settings", json={"values": {"channel_handle": ""}})
    assert r.status_code == 200
    assert r.json()["channel_handle"] == ""


def test_patch_settings_montage_accepts_and_persists_0_and_1(client):
    r = client.patch("/api/settings", json={"values": {"montage": "0"}})
    assert r.status_code == 200
    assert r.json()["montage"] == "0"
    r = client.patch("/api/settings", json={"values": {"montage": "1"}})
    assert r.status_code == 200
    assert r.json()["montage"] == "1"


def test_patch_settings_montage_rejects_invalid_value_422(client):
    r = client.patch("/api/settings", json={"values": {"montage": "oui"}})
    assert r.status_code == 422


def test_patch_settings_cut_style_accepts_and_persists_each_value(client):
    for style in ("doux", "standard", "nerveux"):
        r = client.patch("/api/settings", json={"values": {"cut_style": style}})
        assert r.status_code == 200
        assert r.json()["cut_style"] == style


def test_patch_settings_cut_style_rejects_invalid_value_422(client):
    r = client.patch("/api/settings", json={"values": {"cut_style": "agressif"}})
    assert r.status_code == 422


def test_patch_settings_montage_and_cut_style_do_not_poison_other_fields(client):
    """Reproduit le scenario du FINDING 1 : un payload melant un reglage deja
    connu (channel_handle) et cut_style ne doit ni rejeter en bloc, ni faire
    disparaitre l'ecriture de channel_handle."""
    r = client.patch(
        "/api/settings",
        json={"values": {"channel_handle": "monstream", "cut_style": "nerveux"}},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["channel_handle"] == "monstream"
    assert body["cut_style"] == "nerveux"


def test_cut_style_suit_exactement_les_seuils_du_moteur(client, monkeypatch):
    """Revue finale V6 (lot C, C7) : l'API tenait sa propre liste des rythmes.
    Elle lit maintenant CUT_THRESHOLDS : un style ajoute ou retire au moteur
    l'est aussi a la validation."""
    def statut(style: str) -> int:
        return client.patch("/api/settings", json={"values": {"cut_style": style}}).status_code

    assert [statut(style) for style in CUT_THRESHOLDS] == [200] * len(CUT_THRESHOLDS)
    monkeypatch.setitem(CUT_THRESHOLDS, "fou", 0.2)
    monkeypatch.delitem(CUT_THRESHOLDS, "doux")
    assert statut("fou") == 200 and statut("doux") == 422


def test_patch_settings_auto_profile_accepts_a_name_and_rejects_a_novel(client):
    ok = client.patch("/api/settings", json={"values": {"auto_profile": "WOLDENM OBS"}})
    assert ok.status_code == 200 and ok.json()["auto_profile"] == "WOLDENM OBS"
    r = client.patch("/api/settings", json={"values": {"auto_profile": "x" * 81}})
    assert r.status_code == 422
