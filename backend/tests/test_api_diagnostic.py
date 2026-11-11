"""Le rapport doit etre transmissible tel quel : complet, et sans aucun secret."""
import importlib
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from clipforge import db as dbmod
from clipforge import usage

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
    """La base que l'API vient d'initialiser, pas celle de conftest."""
    from clipforge import config
    c = dbmod.get_db(config.DB_PATH)
    yield c
    c.close()


def test_report_contains_every_block(client, conn, tmp_path):
    """Chaque bloc exige par la spec, asserte un par un.

    L'ancienne version de ce test cherchait la chaine « ClipForge », que le
    TITRE du rapport satisfait a lui seul : la version de l'application, la
    taille de la source et son etat de fragmentation pouvaient manquer sans
    que rien ne tombe. Or ce sont exactement les mesures qui separent
    « ffmpeg absent » de « conteneur non indexe » de « disque plein » —
    c'est-a-dire la raison d'etre du rapport.
    """
    source = tmp_path / "vod.mp4"
    source.write_bytes(b"\x00" * 3_000_000)
    vod_id = dbmod.create_vod(conn, title="Ma VOD", source_path=str(source))
    conn.execute("UPDATE vods SET duration_s=7200, width=1920, height=1080 WHERE id=?",
                 (vod_id,))
    conn.commit()
    dbmod.set_vod_error(conn, vod_id, "Telechargement : Twitch a refuse",
                        "downloading: HTTP 403\nTrace...")
    usage.record(conn, "clip_exported", vod_id=vod_id, rank=17, top_signal="chat")

    texte = client.get("/api/diagnostic").json()["contenu"]

    # Version de ClipForge ET du schema : savoir sur quel code tourne l'installation.
    assert re.search(r"ClipForge : \d+\.\d+\.\d+", texte), texte.splitlines()[:6]
    assert f"Schema de base : version {dbmod.CURRENT_SCHEMA_VERSION}" in texte
    # Etape echouee, message court, detail technique.
    assert "Telechargement : Twitch a refuse" in texte
    assert "downloading: HTTP 403" in texte
    # Etat machine : espace disque, memoire libre, GPU, ffmpeg reellement resolu.
    assert "--- Machine ---" in texte
    for cle in ("ffmpeg", "ffmpeg_chemin", "gpu", "disque_libre_go", "memoire_libre_go"):
        assert f"{cle} :" in texte
    # Fiche de la VOD : duree, definition, TAILLE, FRAGMENTE OU NON, mots, candidats.
    assert "Ma VOD" in texte
    assert "7200 s" in texte
    assert "1920x1080" in texte
    assert "2.9 Mo" in texte, "taille du fichier source absente de la fiche"
    assert "navigable" in texte, "etat du conteneur absent de la fiche"
    assert "mots" in texte and "candidats" in texte
    # Vingt dernieres lignes du journal moteur.
    assert "--- Journal moteur ---" in texte
    # Usage recent.
    assert "clip_exported" in texte and "rang=17" in texte and "signal=chat" in texte


def test_the_report_names_the_size_of_a_source_it_cannot_read(client, conn, tmp_path):
    """Une source disparue est justement ce qu'on vient chercher dans le rapport :
    la fiche doit le DIRE, pas taire la ligne ni emporter le rapport."""
    dbmod.create_vod(conn, title="disparue", source_path=str(tmp_path / "absente.mp4"))
    texte = client.get("/api/diagnostic").json()["contenu"]
    assert "disparue" in texte
    assert "absente.mp4" in texte
    assert "illisible" in texte


def test_a_fragmented_source_is_named_as_such(client, conn, tmp_path, monkeypatch):
    """« Conteneur non indexe » est l'un des trois diagnostics que ce bloc doit
    permettre de distinguer ; le piege le plus couteux de la V3."""
    source = tmp_path / "vod.mp4"
    source.write_bytes(b"\x00" * 1024)
    dbmod.create_vod(conn, title="fragmentee", source_path=str(source))
    monkeypatch.setattr("clipforge.pipeline.remux.is_fragmented", lambda _: True)
    assert "non indexe" in client.get("/api/diagnostic").json()["contenu"]


def test_report_never_leaks_an_environment_value(client, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", SENTINELLE)
    reponse = client.get("/api/diagnostic").json()
    assert SENTINELLE not in reponse["contenu"]
    assert "presente" in reponse["contenu"]
    # Le fichier est ce qui part dans un message : le contrat vaut d'abord pour lui.
    assert SENTINELLE not in Path(reponse["chemin"]).read_text(encoding="utf-8")


def test_the_report_is_written_where_the_response_says_it_is(client):
    """Le bouton ne rend qu'un chemin : si le fichier n'y est pas, il ne sert a rien."""
    reponse = client.get("/api/diagnostic").json()
    fichier = Path(reponse["chemin"])
    assert fichier.is_file()
    assert fichier.read_text(encoding="utf-8") == reponse["contenu"]


def test_report_works_without_any_vod(client):
    """Le bouton est accessible en permanence : sans VOD il doit repondre."""
    reponse = client.get("/api/diagnostic")
    assert reponse.status_code == 200
    assert reponse.json()["contenu"]
