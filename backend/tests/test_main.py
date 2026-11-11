import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from clipforge import db as dbmod
from clipforge.job_cache import purge_stale_job_caches
from clipforge.render import livraison

EXPOSANT_DEUX = chr(178)


@pytest.fixture()
def fresh_main(tmp_path, monkeypatch):
    monkeypatch.setenv("CLIPFORGE_DATA", str(tmp_path))
    import importlib

    from clipforge import config
    importlib.reload(config)
    from clipforge import main as mainmod
    importlib.reload(mainmod)
    return mainmod


@pytest.fixture()
def client(fresh_main):
    with TestClient(fresh_main.app, base_url="http://127.0.0.1:8765") as c:
        yield c


def test_middleware_host_origin_legitimes_200(client):
    r = client.get("/api/health", headers={"Origin": "http://localhost:5173"})
    assert r.status_code == 200


def test_middleware_origin_evil_403(client):
    r = client.get("/api/health", headers={"Origin": "https://evil.com"})
    assert r.status_code == 403


def test_middleware_host_evil_403(client):
    r = client.get("/api/health", headers={"Host": "evil.com"})
    assert r.status_code == 403


def test_reconcile_orphan_statuses_on_startup(fresh_main):
    from clipforge import config

    conn = dbmod.get_db(config.DB_PATH)
    vod_id = dbmod.create_vod(conn, title="t", source_path="x.mp4")
    conn.execute("UPDATE vods SET status='transcribing' WHERE id=?", (vod_id,))
    conn.execute(
        "INSERT INTO clips (vod_id, start_s, end_s, status) VALUES (?,?,?,'rendering')",
        (vod_id, 0.0, 10.0))
    conn.commit()
    conn.close()
    with TestClient(fresh_main.app, base_url="http://127.0.0.1:8765") as c:
        vod = c.get(f"/api/vods/{vod_id}").json()
        assert vod["status"] == "error"
        assert "interrompue" in vod["error"].lower()
        # le detail garde l'etape interrompue : sinon il resterait celui d'un echec anterieur
        assert vod["error_detail"] == "transcribing: interrompu par redemarrage"
        clip = c.get("/api/clips").json()[0]
        assert clip["status"] == "error"
        assert clip["error"] == "interrompu par redemarrage"


def test_reconcile_flags_a_vod_interrupted_during_the_auto_render(fresh_main):
    """Les etapes du mode auto sont des statuts de pipeline comme les autres.

    Elles ne survivent pas plus a un redemarrage que la transcription : le pool
    est perdu, personne ne finira le montage. Le detail garde l'etape, donc la
    relance repart du montage et ne re-transcrit rien.
    """
    from clipforge import config

    conn = dbmod.get_db(config.DB_PATH)
    vod_id = dbmod.create_vod(conn, title="t", source_path="x.mp4")
    conn.execute("UPDATE vods SET status='rendering_clips' WHERE id=?", (vod_id,))
    conn.commit()
    conn.close()
    with TestClient(fresh_main.app, base_url="http://127.0.0.1:8765") as c:
        vod = c.get(f"/api/vods/{vod_id}").json()
    assert vod["status"] == "error"
    assert vod["error_detail"] == "rendering_clips: interrompu par redemarrage"
    # la boucle se ferme ici : ce que la reconciliation ecrit, la reprise le relit
    from clipforge.pipeline.runner import resume_step
    assert resume_step(vod) == "rendering_clips"


def test_warmup_runs_in_background(fresh_main, monkeypatch):
    mainmod = fresh_main
    calls = []

    def fake_warmup():
        calls.append("done")

    monkeypatch.setattr(mainmod, "_warmup_gpu", fake_warmup)
    with TestClient(mainmod.app, base_url="http://127.0.0.1:8765") as client:
        assert client.get("/api/health").status_code == 200
    # le thread de warmup a ete lance (join implicite au shutdown du lifespan)
    assert calls == ["done"]


def test_warmup_stops_at_the_door_when_disabled(fresh_main, monkeypatch):
    """Le garde-fou memoire doit rendre la main AVANT tout chargement de modele.

    On piege l'import de faster_whisper : s'il est atteint, le prechauffage a
    depasse le point ou il devait s'arreter.
    """
    monkeypatch.setenv("CLIPFORGE_NO_WARMUP", "1")
    monkeypatch.setattr(fresh_main, "register_cuda_dlls", None, raising=False)
    import builtins
    vrai_import = builtins.__import__

    def import_piege(nom, *a, **kw):
        if nom.startswith("faster_whisper"):
            raise AssertionError("le prechauffage a charge le modele malgre CLIPFORGE_NO_WARMUP")
        return vrai_import(nom, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", import_piege)
    fresh_main._warmup_gpu()


@pytest.fixture()
def jobs(fresh_main):
    """Base fraiche, une VOD, un clip, et le dossier des jobs. Rend
    (conn, jobs_dir, vod_id, clip_id) ; la connexion est fermee a la sortie."""
    from clipforge import config

    conn = dbmod.get_db(config.DB_PATH)
    vod_id = dbmod.create_vod(conn, title="t", source_path="x.mp4")
    clip_id = conn.execute("INSERT INTO clips (vod_id, start_s, end_s) VALUES (?,?,?)",
                           (vod_id, 0.0, 10.0)).lastrowid
    conn.commit()
    config.JOBS_DIR.mkdir(parents=True, exist_ok=True)
    yield conn, config.JOBS_DIR, vod_id, clip_id
    conn.close()


def test_la_purge_retire_les_dossiers_de_clips_orphelins(jobs):
    """Revue finale V6 (lot C, C8) : montage_plan ecrit jobs/clip<id>/clip_audio.wav
    (32 ko par seconde de clip) et la purge du demarrage ne visait que jobs/vod*.
    Le dossier d'un clip supprime part ; celui d'un clip existant reste, WAV
    compris ; un nom non numerique n'est pas a nous."""
    conn, jobs_dir, _vod_id, clip_id = jobs
    dossiers = {nom: jobs_dir / nom
                for nom in (f"clip{clip_id}", f"clip{clip_id + 1}", "clip", "clipboard")}
    for dossier in dossiers.values():
        dossier.mkdir(parents=True)
        (dossier / "clip_audio.wav").write_bytes(b"RIFF")

    purge_stale_job_caches(conn, jobs_dir)

    assert (dossiers[f"clip{clip_id}"] / "clip_audio.wav").is_file()
    assert not dossiers[f"clip{clip_id + 1}"].exists()
    assert dossiers["clip"].is_dir() and dossiers["clipboard"].is_dir()


@pytest.mark.parametrize("prefixe", ["clip", "vod"])
def test_un_dossier_au_chiffre_unicode_est_ignore_sans_lever(jobs, prefixe):
    """Revue finale V6 (fix round 1, R6) : isdigit() est vrai des chiffres
    unicode. Un dossier au 2 en exposant passait le filtre puis faisait lever
    ValueError a int(), au demarrage de l'application."""
    conn, jobs_dir, _vod_id, _clip_id = jobs
    exotique = jobs_dir / (prefixe + EXPOSANT_DEUX)
    exotique.mkdir(parents=True)
    (exotique / "a moi.txt").write_text("perso", encoding="utf-8")

    purge_stale_job_caches(conn, jobs_dir)

    assert (exotique / "a moi.txt").read_text(encoding="utf-8") == "perso"


def test_un_fichier_inconnu_dans_un_job_orphelin_est_garde_et_journalise(jobs, caplog):
    """Revue finale V6 (fix round 1, R6) : rmtree effacait TOUT le dossier alors
    que l'exception a la regle « jamais de suppression definitive » ne couvre
    que des caches nommes. Un fichier depose la par l'utilisateur partait."""
    conn, jobs_dir, _vod_id, clip_id = jobs
    orphelin = jobs_dir / f"clip{clip_id + 1}"
    orphelin.mkdir(parents=True)
    (orphelin / "clip_audio.wav").write_bytes(b"RIFF")
    (orphelin / "captions.ass").write_text("[Script Info]", encoding="utf-8")
    (orphelin / "mes vacances.mp4").write_bytes(b"a moi")

    with caplog.at_level("WARNING"):
        purge_stale_job_caches(conn, jobs_dir)

    assert orphelin.is_dir()
    assert (orphelin / "mes vacances.mp4").is_file()
    assert not (orphelin / "clip_audio.wav").exists()
    assert not (orphelin / "captions.ass").exists()
    assert "mes vacances.mp4" in caplog.text, caplog.text


def test_un_vod_orphelin_perd_ses_caches_nommes_et_son_dossier(jobs):
    conn, jobs_dir, vod_id, _clip_id = jobs
    orphelin = jobs_dir / f"vod{vod_id + 1}"
    (orphelin / "thumbs").mkdir(parents=True)
    (orphelin / "previews").mkdir(parents=True)
    (orphelin / "thumbs" / "t1.0.jpg").write_bytes(b"jpg")
    (orphelin / "previews" / "t1.0.mp4").write_bytes(b"mp4")
    (orphelin / "audio.wav").write_bytes(b"RIFF")
    (orphelin / "chat.json").write_text("[]", encoding="utf-8")
    (orphelin / "frame_1.0.png").write_bytes(b"png")

    purge_stale_job_caches(conn, jobs_dir)

    assert not orphelin.exists()


def test_un_echec_de_suppression_est_journalise(jobs, caplog, monkeypatch):
    """ignore_errors=True avalait les echecs : une purge qui n'a rien purge
    avait exactement l'air d'une purge reussie."""
    conn, jobs_dir, _vod_id, clip_id = jobs
    orphelin = jobs_dir / f"clip{clip_id + 1}"
    orphelin.mkdir(parents=True)
    (orphelin / "clip_audio.wav").write_bytes(b"RIFF")

    def refuse(self, missing_ok=False):
        raise PermissionError("fichier ouvert par un autre process")

    monkeypatch.setattr(Path, "unlink", refuse)
    with caplog.at_level("WARNING"):
        purge_stale_job_caches(conn, jobs_dir)

    assert (orphelin / "clip_audio.wav").is_file()
    assert "clip_audio.wav" in caplog.text, caplog.text
    assert "non supprime : fichier ouvert par un autre process" in caplog.text, caplog.text


def _dehors(jobs_dir: Path) -> Path:
    """Un dossier de l'utilisateur HORS de jobs/, qui porte des noms de caches."""
    dehors = jobs_dir.parent / "dehors"
    dehors.mkdir()
    (dehors / "captions.ass").write_text("mon fichier", encoding="utf-8")
    (dehors / "clip_audio.wav").write_bytes(b"RIFF a moi")
    return dehors


def _verifier_dehors_intact(dehors: Path, lien: Path, caplog) -> None:
    assert (dehors / "captions.ass").read_text(encoding="utf-8") == "mon fichier"
    assert (dehors / "clip_audio.wav").read_bytes() == b"RIFF a moi"
    assert lien.exists()
    assert f"{lien.name} ignore : lien ou jonction" in caplog.text, caplog.text


@pytest.mark.skipif(sys.platform != "win32", reason="jonctions NTFS")
def test_une_jonction_dans_jobs_n_est_jamais_suivie(jobs, caplog):
    """Fix round 2 (K5) : une jonction jobs/clip9 vers un dossier quelconque
    faisait supprimer les caches nommes DANS LA CIBLE, hors de jobs/ (mesure du
    relecteur : captions.ass de la cible supprime)."""
    import _winapi

    conn, jobs_dir, _vod_id, _clip_id = jobs
    dehors = _dehors(jobs_dir)
    lien = jobs_dir / "clip9"
    _winapi.CreateJunction(str(dehors), str(lien))
    assert os.path.isjunction(lien)
    with caplog.at_level("WARNING"):
        purge_stale_job_caches(conn, jobs_dir)
    _verifier_dehors_intact(dehors, lien, caplog)


def test_un_lien_symbolique_dans_jobs_n_est_jamais_suivi(jobs, caplog):
    """Meme garde pour un lien symbolique : tourne en CI (Linux), ou la
    jonction n'existe pas."""
    conn, jobs_dir, _vod_id, _clip_id = jobs
    dehors = _dehors(jobs_dir)
    lien = jobs_dir / "vod9"
    try:
        lien.symlink_to(dehors, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"lien symbolique non creable ici : {exc}")
    (dehors / "audio.wav").write_bytes(b"RIFF a moi aussi")
    with caplog.at_level("WARNING"):
        purge_stale_job_caches(conn, jobs_dir)
    _verifier_dehors_intact(dehors, lien, caplog)
    assert (dehors / "audio.wav").read_bytes() == b"RIFF a moi aussi"


def test_un_id_rembourre_n_est_pas_un_id(jobs):
    """Fix round 2 (K5) : clip0000000000000000002 etait pris pour le clip 2
    (supprime de la base) et purge ; ClipForge n'ecrit que la forme canonique."""
    conn, jobs_dir, _vod_id, clip_id = jobs
    noms = (f"clip0{clip_id}", f"clip{clip_id + 1:019d}", f"vod{0:03d}")
    for nom in noms:
        (jobs_dir / nom).mkdir()
        (jobs_dir / nom / "clip_audio.wav").write_bytes(b"RIFF")
        (jobs_dir / nom / "audio.wav").write_bytes(b"RIFF")

    purge_stale_job_caches(conn, jobs_dir)

    for nom in noms:
        assert (jobs_dir / nom / "clip_audio.wav").read_bytes() == b"RIFF", nom
        assert (jobs_dir / nom / "audio.wav").read_bytes() == b"RIFF", nom


def test_les_rendus_mis_de_cote_sont_a_nous_une_ligne_par_demarrage(jobs, caplog):
    """Fix round 2 (K5) : les refus-*.mp4 d'un clip supprime etaient annonces
    « n'est pas a nous » a chaque demarrage. Ils restent (jamais de
    suppression) ; une ligne INFO dit combien et ou."""
    conn, jobs_dir, _vod_id, clip_id = jobs
    orphelin = jobs_dir / f"clip{clip_id + 1}"
    orphelin.mkdir()
    ranges = ("refus-nvenc.mp4", "refus-cpu (2).mp4", "pret-non-promu.mp4",
              "orphelin.mp4", "non-controle-cpu.mp4")
    for nom in ranges:
        (orphelin / nom).write_bytes(b"rendu")
    (orphelin / "mes vacances.mp4").write_bytes(b"a moi")

    with caplog.at_level("INFO"):
        purge_stale_job_caches(conn, jobs_dir)

    assert all((orphelin / nom).is_file() for nom in ranges)
    avertissements = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    assert len(avertissements) == 1 and "mes vacances.mp4" in avertissements[0], avertissements
    assert not any(nom in avertissements[0] for nom in ranges), avertissements
    infos = [r.getMessage() for r in caplog.records if r.levelname == "INFO"]
    assert len(infos) == 1 and "5 rendu(s) mis de cote" in infos[0], infos
    assert f"clip{clip_id + 1} : 5" in infos[0], infos


def test_un_refus_d_une_revision_precedente_est_compte_comme_le_notre(jobs, caplog):
    """Fix round 3 (F3) : le motif ne connaissait que refus-nvenc et refus-cpu,
    donc un refus-orphelin.mp4 pose par une revision precedente etait annonce
    « pas a nous » a chaque demarrage, et son dossier garde sans un mot sur ce
    qu'il y a a trier."""
    conn, jobs_dir, _vod_id, clip_id = jobs
    orphelin = jobs_dir / f"clip{clip_id + 1}"
    orphelin.mkdir(parents=True)
    (orphelin / "refus-orphelin.mp4").write_bytes(b"mp4")

    with caplog.at_level("INFO"):
        purge_stale_job_caches(conn, jobs_dir)

    assert (orphelin / "refus-orphelin.mp4").is_file()
    assert "n'est pas a nous" not in caplog.text, caplog.text
    assert "a trier a la main" in caplog.text, caplog.text


def test_la_purge_range_d_abord_les_temporaires_de_rendu(jobs):
    """Fix round 3 (F3) : le demarrage ne ramassait pas les temporaires laisses
    dans le dossier de livraison par un rendu tue ; celui d'un clip supprime
    depuis ne bougeait plus jamais. Le verrou pose pour verifier qu'aucun rendu
    ne vit repart avec la purge."""
    conn, jobs_dir, _vod_id, clip_id = jobs
    from clipforge import config

    config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    vivant = livraison.part_path(config.OUTPUT_DIR, clip_id)
    disparu = livraison.part_path(config.OUTPUT_DIR, clip_id + 1)
    for part in (vivant, disparu):
        part.write_text("mp4 3.0", encoding="utf-8")

    purge_stale_job_caches(conn, jobs_dir)

    assert not vivant.exists() and not disparu.exists()
    assert (jobs_dir / f"clip{clip_id}" / "orphelin.mp4").is_file()
    assert (jobs_dir / "_poubelle" / f"clip{clip_id + 1}-orphelin.mp4").is_file()
    assert not (jobs_dir / f"clip{clip_id + 1}").exists()
