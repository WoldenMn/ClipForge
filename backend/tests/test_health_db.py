"""Controles de sante portant sur les DONNEES : schema, sources, indexation.

Separes des controles machine (test_health.py) le long de la meme couture que
la production : `run_db_checks` prend une connexion, `run_checks_without_db`
n'en veut pas. Meme regle de module des deux cotes — un controle qui echoue
porte SON REMEDE.
"""
from clipforge import config, health
from clipforge import db as dbmod


def _controle(controles: list[dict], cle: str) -> dict:
    return next(c for c in controles if c["cle"] == cle)

def test_a_database_at_the_current_version_passes(conn):
    coherence = _controle(health.run_db_checks(conn), "schema")
    assert coherence["ok"] is True
    assert str(dbmod.CURRENT_SCHEMA_VERSION) in coherence["detail"]


def test_a_database_behind_the_engine_is_reported_with_what_to_do(conn):
    """« Coherence de la base », exigee par la spec. Une base restee en arriere
    fait echouer les requetes du moteur sur des colonnes qui n'existent pas."""
    conn.execute("UPDATE schema_version SET version=3")
    conn.commit()
    coherence = _controle(health.run_db_checks(conn), "schema")
    assert coherence["ok"] is False
    assert "3" in coherence["detail"]
    assert str(dbmod.CURRENT_SCHEMA_VERSION) in coherence["detail"]
    assert coherence["action"]


def test_an_unreadable_schema_version_is_described_not_raised(conn):
    """Meme regle que _espace_libre : lever ici emporterait TOUS les controles,
    et la banniere se tairait au moment ou la base est justement cassee."""
    conn.execute("DROP TABLE schema_version")
    conn.commit()
    coherence = _controle(health.run_db_checks(conn), "schema")
    assert coherence["ok"] is False
    assert coherence["action"]


def test_a_missing_source_file_is_reported_with_its_title(conn, tmp_path):
    dbmod.create_vod(conn, title="disparue", source_path=str(tmp_path / "absente.mp4"))
    sources = _controle(health.run_db_checks(conn), "sources")
    assert sources["ok"] is False
    assert "disparue" in sources["detail"]
    assert sources["action"]


def test_a_vod_not_downloaded_yet_is_not_a_missing_source(conn):
    """Une VOD Twitch avant telechargement n'a pas de fichier : l'annoncer
    disparue apprendrait au user a ignorer la banniere."""
    dbmod.create_vod(conn, title="a telecharger",
                     twitch_url="https://www.twitch.tv/videos/1")
    assert _controle(health.run_db_checks(conn), "sources")["ok"] is True


def test_a_fragmented_download_is_reported_with_the_command_that_repairs_it(
        conn, tmp_path, monkeypatch):
    """Le piege le plus couteux de la V3 doit se voir AVANT une analyse."""
    monkeypatch.setattr(config, "DOWNLOADS_DIR", tmp_path)
    source = tmp_path / "vod.mp4"
    source.write_bytes(b"x")
    dbmod.create_vod(conn, title="fragmentee", source_path=str(source))
    monkeypatch.setattr("clipforge.pipeline.remux.is_fragmented", lambda _: True)
    indexation = _controle(health.run_db_checks(conn), "indexation")
    assert indexation["ok"] is False
    assert "fragmentee" in indexation["detail"]
    assert "migrate_seekable" in indexation["action"]


def test_a_fragmented_file_outside_the_downloads_is_not_flagged(
        conn, tmp_path, monkeypatch):
    """migrate_seekable et step_remux ne reecrivent QUE ce que ClipForge a
    telecharge : un import local appartient au user. Afficher cette commande sur
    un fichier qu'elle ignore serait un remede qui ne repare rien."""
    monkeypatch.setattr(config, "DOWNLOADS_DIR", tmp_path / "telecharges")
    source = tmp_path / "import_perso.mp4"
    source.write_bytes(b"x")
    dbmod.create_vod(conn, title="import perso", source_path=str(source))
    monkeypatch.setattr("clipforge.pipeline.remux.is_fragmented", lambda _: True)
    assert _controle(health.run_db_checks(conn), "indexation")["ok"] is True


def test_a_vod_the_pipeline_holds_is_never_told_to_close_the_app(
        conn, tmp_path, monkeypatch):
    """step_download ecrit source_path (steps.py:44) puis rend la main a
    step_remux, qui passe des minutes sur plusieurs Go : pendant toute cette
    fenetre le fichier est fragmente ET sous DOWNLOADS_DIR.

    Le signaler collerait « Ferme ClipForge » sur une analyse en cours. Le user
    qui suit l'instruction interrompt son analyse pour relancer en CLI le
    travail que le pipeline etait justement en train de faire.
    """
    monkeypatch.setattr(config, "DOWNLOADS_DIR", tmp_path)
    source = tmp_path / "vod.mp4"
    source.write_bytes(b"x")
    vod_id = dbmod.create_vod(conn, title="en cours", source_path=str(source))
    dbmod.set_vod_status(conn, vod_id, "remuxing")
    monkeypatch.setattr("clipforge.pipeline.remux.is_fragmented", lambda _: True)
    assert _controle(health.run_db_checks(conn), "indexation")["ok"] is True


def test_a_source_that_vanishes_during_an_analysis_is_still_reported(conn, tmp_path):
    """La detente ne vaut que pour l'indexation : un fichier disparu sous les
    pieds du pipeline est un vrai probleme, quelle que soit l'etape en cours."""
    vod_id = dbmod.create_vod(conn, title="disparue en route",
                              source_path=str(tmp_path / "absente.mp4"))
    dbmod.set_vod_status(conn, vod_id, "transcribing")
    sources = _controle(health.run_db_checks(conn), "sources")
    assert sources["ok"] is False
    assert "disparue en route" in sources["detail"]


def test_a_source_gone_from_the_disk_is_never_probed_for_fragmentation(
        conn, tmp_path, monkeypatch):
    """Un seul defaut par VOD : sonder le conteneur d'un fichier absent la
    ferait apparaitre dans les deux controles pour une seule cause."""
    monkeypatch.setattr(config, "DOWNLOADS_DIR", tmp_path)
    dbmod.create_vod(conn, title="disparue", source_path=str(tmp_path / "absente.mp4"))

    def jamais(_path):
        raise AssertionError("conteneur sonde alors que le fichier n'existe pas")

    monkeypatch.setattr("clipforge.pipeline.remux.is_fragmented", jamais)
    assert _controle(health.run_db_checks(conn), "indexation")["ok"] is True
