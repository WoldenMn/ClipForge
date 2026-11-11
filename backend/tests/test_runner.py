import struct
import subprocess
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

from clipforge import db as dbmod
from clipforge.pipeline import remux
from clipforge.pipeline.runner import (
    STEP_LABELS,
    STEP_ORDER,
    resume_step,
    run_analysis_pipeline,
    run_steps,
)
from clipforge.pipeline.steps import build_real_steps

FIXTURE = Path(__file__).parent / "fixtures" / "fixture.mp4"


def test_run_steps_executes_in_order_and_reaches_ready(conn):
    vod_id = dbmod.create_vod(conn, title="t", source_path="x.mp4")
    calls = []
    steps = [(name, lambda c, v, ctx, n=name: calls.append(n)) for name in STEP_ORDER]
    run_steps(conn, vod_id, steps, ctx={})
    assert calls == list(STEP_ORDER)
    assert dbmod.get_vod(conn, vod_id)["status"] == "ready"


def test_run_steps_resumes_after_failure(conn):
    vod_id = dbmod.create_vod(conn, title="t", source_path="x.mp4")
    boom = {"armed": True}
    calls = []

    def failing(c, v, ctx):
        calls.append("transcribing")
        if boom["armed"]:
            raise RuntimeError("boom")

    steps = [(n, (failing if n == "transcribing" else
                  (lambda c, v, ctx, n=n: calls.append(n)))) for n in STEP_ORDER]
    run_steps(conn, vod_id, steps, ctx={})
    vod = dbmod.get_vod(conn, vod_id)
    assert vod["status"] == "error"
    assert vod["error_detail"].startswith("transcribing:")  # la reprise lit le detail technique
    boom["armed"] = False
    calls.clear()
    # le point de reprise se calcule AVANT toute reecriture du statut, comme le fait l'API
    reprise = resume_step(vod)
    run_steps(conn, vod_id, steps, ctx={}, resume_from=reprise)
    assert calls[0] == "transcribing"
    assert "extracting_audio" not in calls
    assert dbmod.get_vod(conn, vod_id)["status"] == "ready"


def test_resume_step_reads_the_failed_step_from_the_technical_detail(conn):
    vod_id = dbmod.create_vod(conn, title="t", source_path="x.mp4")
    dbmod.set_vod_error(conn, vod_id, "Transcription : GPU indisponible", "transcribing: boom")
    assert resume_step(dbmod.get_vod(conn, vod_id)) == "transcribing"


def test_resume_step_replays_everything_when_the_detail_names_no_step(conn):
    """Un detail technique qui ne commence pas par une etape ne fait rien sauter.

    Le pendant sur le vrai format ecrit par _log_pool_failure est dans
    test_api_vods.py : ici on ne teste que la fonction, pas le couplage.
    """
    vod_id = dbmod.create_vod(conn, title="t", source_path="x.mp4")
    dbmod.set_vod_error(conn, vod_id, "Analyse interrompue", "sans prefixe d'etape")
    assert resume_step(dbmod.get_vod(conn, vod_id)) is None


def test_resume_step_uses_the_status_when_it_stayed_on_a_step(conn):
    """Crash brut sans passage par set_vod_error : le statut porte encore l'etape."""
    vod_id = dbmod.create_vod(conn, title="t", source_path="x.mp4")
    dbmod.set_vod_status(conn, vod_id, "analyzing")
    assert resume_step(dbmod.get_vod(conn, vod_id)) == "analyzing"


def test_resume_step_replays_everything_on_a_finished_or_fresh_vod(conn):
    """Relancer une VOD prete (nouveaux poids, scoring IA active) rejoue tout."""
    prete = dbmod.create_vod(conn, title="prete", source_path="x.mp4")
    dbmod.set_vod_status(conn, prete, "ready", progress=100.0)
    neuve = dbmod.create_vod(conn, title="neuve", source_path="y.mp4")
    assert resume_step(dbmod.get_vod(conn, prete)) is None
    assert resume_step(dbmod.get_vod(conn, neuve)) is None


def test_run_steps_skips_everything_before_the_resume_point(conn):
    vod_id = dbmod.create_vod(conn, title="t", source_path="x.mp4")
    calls = []
    steps = [(name, lambda c, v, ctx, n=name: calls.append(n)) for name in STEP_ORDER]
    run_steps(conn, vod_id, steps, ctx={}, resume_from="segmenting")
    assert calls == ["segmenting", "ai_scoring", "making_clips", "writing_hooks",
                     "rendering_clips"]
    assert dbmod.get_vod(conn, vod_id)["status"] == "ready"


def test_run_analysis_pipeline_forwards_the_resume_point(tmp_path, monkeypatch):
    """Le point de reprise doit traverser le point d'entree du process enfant.

    C'est le maillon qui manquait : sans lui, l'API a beau calculer une reprise,
    le pipeline rejoue tout depuis le debut sans que rien ne le signale.
    """
    db_path = tmp_path / "pipeline.db"
    conn = dbmod.get_db(db_path)
    vod_id = dbmod.create_vod(conn, title="t", source_path="x.mp4")
    conn.close()
    calls = []
    # patch sur steps, pas sur runner : run_analysis_pipeline importe tardivement
    monkeypatch.setattr(
        "clipforge.pipeline.steps.build_real_steps",
        lambda: [(n, lambda c, v, ctx, n=n: calls.append(n)) for n in STEP_ORDER])
    # Les etapes sont fausses : les exigences reelles (source, audio) ne gardent
    # plus rien et refuseraient de les laisser tourner. Leur transmission est
    # verifiee a part, dans test_preconditions.py.
    monkeypatch.setattr("clipforge.pipeline.preconditions.build_preconditions", dict)
    run_analysis_pipeline(vod_id, str(db_path), "analyzing")
    assert calls == ["analyzing", "segmenting", "ai_scoring", "making_clips",
                     "writing_hooks", "rendering_clips"]


def test_build_real_steps_matches_step_order(conn):
    """resume_step arbitre sur STEP_ORDER : les deux listes ne peuvent pas diverger."""
    assert [name for name, _ in build_real_steps()] == list(STEP_ORDER)


def test_step_failure_keeps_the_trace_out_of_the_user_message(conn):
    """error : court, francais, sans pile d'appels. error_detail : la technique."""
    vod_id = dbmod.create_vod(conn, title="t", source_path="x.mp4")

    def failing(c, v, ctx):
        raise RuntimeError("Library cublas64_12.dll is not found")

    steps = [(n, (failing if n == "transcribing" else (lambda c, v, ctx: None)))
             for n in STEP_ORDER]
    run_steps(conn, vod_id, steps, ctx={})
    vod = dbmod.get_vod(conn, vod_id)
    assert vod["error"].startswith("Transcription")
    assert "GPU" in vod["error"]
    assert "Traceback" not in vod["error"] and "cublas" not in vod["error"]
    assert "Traceback" in vod["error_detail"] and "cublas" in vod["error_detail"]
    assert vod["step_label"] == "Transcription"


def test_run_steps_labels_the_step_before_running_it(conn):
    vod_id = dbmod.create_vod(conn, title="t", source_path="x.mp4")
    seen = {}

    def capture(c, v, ctx):
        seen["label"] = dbmod.get_vod(c, v)["step_label"]

    steps = [(n, (capture if n == "segmenting" else (lambda c, v, ctx: None)))
             for n in STEP_ORDER]
    run_steps(conn, vod_id, steps, ctx={})
    assert seen["label"] == "Découpage"


def test_resume_skips_completed_transcription(conn):
    vod_id = dbmod.create_vod(conn, title="t", source_path="x.mp4")
    conn.execute("UPDATE vods SET duration_s=?, words_complete=1 WHERE id=?", (30.0, vod_id))
    conn.executemany(
        "INSERT INTO words VALUES (?,?,?,?,?,?)",
        [(vod_id, i, float(i), float(i) + 0.5, "mot", 0.9) for i in range(30)])
    conn.commit()
    step_transcribe = dict(build_real_steps())["transcribing"]
    step_transcribe(conn, vod_id, {})
    n = conn.execute("SELECT COUNT(*) AS n FROM words WHERE vod_id=?", (vod_id,)).fetchone()["n"]
    assert n == 30


def test_step_transcribe_sets_words_complete_after_success(conn, tmp_path, monkeypatch):
    from clipforge import config

    monkeypatch.setattr(config, "JOBS_DIR", tmp_path)
    vod_id = dbmod.create_vod(conn, title="t", source_path="x.mp4")
    conn.execute("UPDATE vods SET duration_s=5 WHERE id=?", (vod_id,))
    conn.commit()
    wav_path = tmp_path / f"vod{vod_id}" / "audio.wav"
    wav_path.parent.mkdir(parents=True)
    with wave.open(str(wav_path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x00" * 16000 * 5)

    class FakeWhisperModel:
        def __init__(self, *args, **kwargs):
            pass

        def transcribe(self, audio, **kwargs):
            seg = SimpleNamespace(end=1.0, words=[
                SimpleNamespace(start=0.0, end=0.4, word=" ok", probability=0.9)])
            return iter([seg]), SimpleNamespace(duration=5.0)

    monkeypatch.setattr("faster_whisper.WhisperModel", FakeWhisperModel)
    step_transcribe = dict(build_real_steps())["transcribing"]
    step_transcribe(conn, vod_id, {})
    assert dbmod.get_vod(conn, vod_id)["words_complete"] == 1


def test_real_step_audio_resumes_with_fresh_ctx(conn, tmp_path, monkeypatch):
    from clipforge import config

    monkeypatch.setattr(config, "JOBS_DIR", tmp_path)
    vod_id = dbmod.create_vod(conn, title="t", source_path="x.mp4")
    wav_path = tmp_path / f"vod{vod_id}" / "audio.wav"
    wav_path.parent.mkdir(parents=True)
    with wave.open(str(wav_path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x00" * 16000)
    step_audio = dict(build_real_steps())["analyzing"]
    step_audio(conn, vod_id, {})
    n = conn.execute("SELECT COUNT(*) AS n FROM signals WHERE vod_id=? AND kind='audio'",
                     (vod_id,)).fetchone()["n"]
    assert n > 0


def test_step_order_has_merged_analyzing():
    assert "analyzing" in STEP_ORDER
    assert "analyzing_audio" not in STEP_ORDER
    assert "analyzing_chat" not in STEP_ORDER
    assert list(STEP_ORDER) == ["downloading", "remuxing", "probing", "extracting_audio",
                                "transcribing", "analyzing", "segmenting", "ai_scoring",
                                "making_clips", "writing_hooks", "rendering_clips"]


def test_the_three_auto_steps_close_the_pipeline_in_order():
    """Fabriquer, ecrire, monter : chacune consomme ce que la precedente produit."""
    assert list(STEP_ORDER[-3:]) == ["making_clips", "writing_hooks", "rendering_clips"]


def test_the_auto_steps_are_labelled_in_french():
    assert STEP_LABELS["making_clips"] == "Fabrication des clips"
    assert STEP_LABELS["writing_hooks"] == "Écriture des accroches"
    assert STEP_LABELS["rendering_clips"] == "Montage final"


def test_every_step_label_is_written_in_proper_french():
    """D4 : la carte affichait le badge « Téléchargement » (STATUS_FR du frontend)
    juste au-dessus du libelle d'etape « Telechargement », dans le meme cadre. Ces
    libelles sont ceux de STATUS_FR (frontend/src/api/client.ts), a la lettre."""
    assert STEP_LABELS["downloading"] == "Téléchargement"
    assert STEP_LABELS["remuxing"] == "Indexation vidéo"
    assert STEP_LABELS["probing"] == "Lecture métadonnées"
    assert STEP_LABELS["segmenting"] == "Découpage"
    assert set(STEP_LABELS) == set(STEP_ORDER)


def test_resume_at_an_auto_step_never_replays_the_transcription(conn):
    """Une VOD tuee pendant le montage repart du montage, pas de la transcription.

    Ajouter des etapes a STEP_ORDER change ce que resume_step considere comme
    « dans le pipeline » : sans ce controle, un crash au dernier metre pourrait
    redemander quatre heures de transcription.
    """
    vod_id = dbmod.create_vod(conn, title="t", source_path="x.mp4")
    dbmod.set_vod_status(conn, vod_id, "rendering_clips")
    vod = dbmod.get_vod(conn, vod_id)
    assert resume_step(vod) == "rendering_clips"
    calls = []
    steps = [(name, lambda c, v, ctx, n=name: calls.append(n)) for name in STEP_ORDER]
    run_steps(conn, vod_id, steps, ctx={}, resume_from=resume_step(vod))
    assert calls == ["rendering_clips"]
    assert dbmod.get_vod(conn, vod_id)["status"] == "ready"


def test_an_auto_step_that_failed_stays_the_resume_point(conn):
    """Le detail technique porte le nom de l'etape : la relance repart de la."""
    vod_id = dbmod.create_vod(conn, title="t", source_path="x.mp4")
    dbmod.set_vod_error(conn, vod_id, "Fabrication des clips : refusee",
                        "making_clips: precondition non satisfaite")
    assert resume_step(dbmod.get_vod(conn, vod_id)) == "making_clips"


def test_remuxing_runs_before_anything_reads_the_file():
    """Le remux reecrit la source : il precede probe, extraction et decoupage."""
    ordre = list(STEP_ORDER)
    assert ordre.index("downloading") < ordre.index("remuxing")
    for lecteur in ("probing", "extracting_audio", "segmenting"):
        assert ordre.index("remuxing") < ordre.index(lecteur)


def test_segment_step_fills_v2_columns(conn, monkeypatch, tmp_path):
    from clipforge import config

    monkeypatch.setattr(config, "JOBS_DIR", tmp_path)
    vod_id = dbmod.create_vod(conn, title="t", source_path="x.mp4")
    conn.execute("UPDATE vods SET duration_s=600 WHERE id=?", (vod_id,))
    for i, t in enumerate([100.0, 101.0, 102.0]):
        conn.execute("INSERT INTO words VALUES (?,?,?,?,?,?)",
                     (vod_id, i, t, t + 0.5, f"mot{i}", 0.9))
    for t in range(0, 600, 5):
        sc = 0.95 if 95 <= t <= 105 else 0.1
        conn.execute("INSERT INTO signals VALUES (?,?,?,?)", (vod_id, "audio", float(t), sc))
        conn.execute("INSERT INTO signals VALUES (?,?,?,?)", (vod_id, "chat", float(t), sc))
    conn.commit()
    step_segment = dict(build_real_steps())["segmenting"]
    step_segment(conn, vod_id, {})
    row = conn.execute(
        "SELECT signals_count, hook_text FROM segments WHERE vod_id=? ORDER BY rank",
        (vod_id,)).fetchone()
    assert row["signals_count"] >= 2
    assert "mot0" in (row["hook_text"] or "")


def test_segment_step_hook_text_recolle_les_elisions(conn, monkeypatch, tmp_path):
    """L'accroche stockee est lue par l'IA et par le repli local : une elision
    coupee ici ressort telle quelle sur la bande camera et dans le titre."""
    from clipforge import config

    monkeypatch.setattr(config, "JOBS_DIR", tmp_path)
    vod_id = dbmod.create_vod(conn, title="t", source_path="x.mp4")
    conn.execute("UPDATE vods SET duration_s=600 WHERE id=?", (vod_id,))
    mots = ["Non", "c", "'est", "pas", "moi", "allez", "-vous"]
    for i, mot in enumerate(mots):
        t = 100.0 + i * 0.5
        conn.execute("INSERT INTO words VALUES (?,?,?,?,?,?)",
                     (vod_id, i, t, t + 0.4, mot, 0.9))
    for t in range(0, 600, 5):
        sc = 0.95 if 95 <= t <= 105 else 0.1
        conn.execute("INSERT INTO signals VALUES (?,?,?,?)", (vod_id, "audio", float(t), sc))
        conn.execute("INSERT INTO signals VALUES (?,?,?,?)", (vod_id, "chat", float(t), sc))
    conn.commit()
    step_segment = dict(build_real_steps())["segmenting"]
    step_segment(conn, vod_id, {})
    hook = conn.execute(
        "SELECT hook_text FROM segments WHERE vod_id=? ORDER BY rank",
        (vod_id,)).fetchone()["hook_text"]
    assert "c'est" in hook
    assert "c 'est" not in hook
    assert "allez-vous" in hook


def _atome(typ: bytes, corps: bytes = b"") -> bytes:
    return struct.pack(">I", 8 + len(corps)) + typ + corps


def _mp4_sain() -> bytes:
    """MP4 minimal INDEXE : ftyp, moov sans mvex, mdat. Ni TS, ni fragmente."""
    return _atome(b"ftyp", b"isom") + _atome(b"moov", _atome(b"mvhd")) + _atome(b"mdat")


def _ts_brut(paquets: int = 8) -> bytes:
    """MPEG-TS synthetique : l'octet de synchro puis 187 octets, en boucle."""
    return (bytes([remux.TS_SYNC]) + b"\x00" * (remux.TS_PACKET - 1)) * paquets


def _ts_reel(dest: Path, duree: str = "10") -> Path:
    """Une vraie capture MPEG-TS, comme le HLS Twitch la livre sous un nom .mp4."""
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(FIXTURE), "-t", duree,
                    "-c", "copy", "-f", "mpegts", str(dest)], check=True)
    return dest


def _ts_tronque(dest: Path) -> Path:
    """Le meme, coupe au quart sur une frontiere de paquet : ffmpeg le copie sans
    broncher et rend la main, c'est le controle de duree qui refuse la sortie —
    la forme exacte de la source du user (1620 s livrees pour 17170 s)."""
    plein = _ts_reel(dest.with_name("plein.ts"))
    octets = plein.read_bytes()
    plein.unlink()
    dest.write_bytes(octets[:len(octets) // 4 // remux.TS_PACKET * remux.TS_PACKET])
    return dest


@pytest.mark.integration
def test_the_two_gestures_heal_a_corrupt_source_by_redownloading_it(conn, tmp_path,
                                                                    monkeypatch):
    """D8 de bout en bout, deux clics, aucune main humaine sur le disque.

    Premier « Reprendre l'analyse » : le remux refuse la source, l'ecarte et vide
    source_path. Second : la reprise repart du TELECHARGEMENT — pas du remux, qui
    n'aurait plus rien a remuxer — une capture saine prend le nom libere, et le
    sondage aboutit enfin. Le fichier ecarte est toujours la, entier : c'est a
    l'utilisateur de decider de s'en debarrasser.
    """
    from clipforge import config
    from clipforge.pipeline import download as dl

    downloads = tmp_path / "downloads"
    downloads.mkdir()
    monkeypatch.setattr(config, "DOWNLOADS_DIR", downloads)
    monkeypatch.setattr(config, "JOBS_DIR", tmp_path / "jobs")
    source = _ts_tronque(downloads / "v2836283171.mp4")
    corrompu = source.read_bytes()
    vod_id = dbmod.create_vod(conn, title="VOD 2836283171", source_path=str(source),
                              twitch_url="https://www.twitch.tv/videos/2836283171")
    conn.execute("UPDATE vods SET duration_s=? WHERE id=?", (10.0, vod_id))
    conn.commit()
    reelles = dict(build_real_steps())
    trois = [(n, reelles[n]) for n in ("downloading", "remuxing", "probing")]

    run_steps(conn, vod_id, trois, ctx={},
              resume_from=resume_step(dbmod.get_vod(conn, vod_id)))

    ecartee = dbmod.get_vod(conn, vod_id)
    assert ecartee["status"] == "error"
    assert ecartee["error"] == ("Indexation vidéo : Source endommagée — fichier mis de "
                                "côté ; relance l'analyse pour retélécharger la VOD.")
    assert "pour 10 s attendues" in ecartee["error_detail"]   # les chiffres restent techniques
    assert ecartee["source_path"] == ""
    assert not source.exists()
    assert (downloads / "v2836283171.corrompu.mp4").read_bytes() == corrompu

    monkeypatch.setattr(dl, "download_vod",
                        lambda url, dest, **kw: (_ts_reel(dest / "v2836283171.mp4"),
                                                 "Un stream", 10.0))
    assert resume_step(ecartee) == "downloading"
    run_steps(conn, vod_id, trois, ctx={}, resume_from=resume_step(ecartee))

    guerie = dbmod.get_vod(conn, vod_id)
    assert guerie["status"] == "ready", guerie["error"]
    assert remux.is_mpegts(Path(guerie["source_path"])) is False
    assert guerie["duration_s"] == pytest.approx(10.0, abs=1.0)
    assert (downloads / "v2836283171.corrompu.mp4").is_file()   # rien n'a ete supprime


def _ligne_du_user(conn, tmp_path, monkeypatch, *, octets: bytes,
                   etape_en_erreur: str) -> int:
    """Rejoue l'etat REEL de la ligne 1 au 2026-08-30 : error, un detail technique
    qui nomme une etape TARDIVE, et un fichier telecharge a source_path."""
    from clipforge import config

    downloads = tmp_path / "downloads"
    downloads.mkdir(exist_ok=True)
    monkeypatch.setattr(config, "DOWNLOADS_DIR", downloads)
    source = downloads / "v2836283171.mp4"
    source.write_bytes(octets)
    vod_id = dbmod.create_vod(conn, title="VOD 2836283171", source_path=str(source),
                              twitch_url="https://www.twitch.tv/videos/2836283171")
    conn.execute("UPDATE vods SET duration_s=? WHERE id=?", (17170.0, vod_id))
    conn.commit()
    dbmod.set_vod_error(
        conn, vod_id,
        f"{STEP_LABELS[etape_en_erreur]} : Durée indisponible",
        f"{etape_en_erreur}: ProbeError: Durée indisponible")
    return vod_id


def test_a_raw_ts_drags_a_late_resume_point_back_to_the_remux(conn, tmp_path, monkeypatch):
    """D1-bis : la ligne du user disait `probing:` AU-DESSUS d'un MPEG-TS brut.

    La reprise sautait donc telechargement ET remux — toute la reparation de
    de8258f — pour relire le meme conteneur et remourir en une seconde, pendant
    que le message promettait « Relance l'analyse : ClipForge le reparera ». Le
    correctif precedent avait ete valide sur une ligne synthetique dont le detail
    nommait `downloading` : la mauvaise condition initiale.
    """
    vod_id = _ligne_du_user(conn, tmp_path, monkeypatch,
                            octets=_ts_brut(), etape_en_erreur="probing")
    vod = dbmod.get_vod(conn, vod_id)
    assert vod["error_detail"].startswith("probing: ")
    assert remux.is_mpegts(Path(vod["source_path"])) is True

    assert resume_step(vod) == "remuxing"
    calls = []
    steps = [(name, lambda c, v, ctx, n=name: calls.append(n)) for name in STEP_ORDER]
    run_steps(conn, vod_id, steps, ctx={}, resume_from=resume_step(vod))
    assert calls[0] == "remuxing"                 # la reparation est REJOUEE
    assert "probing" in calls                     # et le sondage vient APRES elle
    assert dbmod.get_vod(conn, vod_id)["status"] == "ready"


def test_the_clamp_holds_however_late_the_step_named_by_the_detail(conn, tmp_path,
                                                                  monkeypatch):
    """Le prochain run empoisonnera la ligne avec l'etape suivante, puis la suivante."""
    for etape in ("extracting_audio", "transcribing", "segmenting", "rendering_clips"):
        vod_id = _ligne_du_user(conn, tmp_path, monkeypatch,
                                octets=_ts_brut(), etape_en_erreur=etape)
        assert resume_step(dbmod.get_vod(conn, vod_id)) == "remuxing"


def test_a_healthy_mp4_keeps_its_late_resume_point(conn, tmp_path, monkeypatch):
    """Aucun remux fantome : une source saine ne ramene RIEN en arriere.

    C'est la garantie « on ne retranscrit pas quatre heures » : la ramener a
    remuxing rejouerait sondage, extraction et analyse pour rien.
    """
    vod_id = _ligne_du_user(conn, tmp_path, monkeypatch,
                            octets=_mp4_sain(), etape_en_erreur="transcribing")
    assert resume_step(dbmod.get_vod(conn, vod_id)) == "transcribing"


def _import_local(conn, tmp_path, monkeypatch, nom: str, octets: bytes) -> int:
    """Une vod importee par l'utilisateur, en erreur tard dans le pipeline."""
    from clipforge import config

    monkeypatch.setattr(config, "DOWNLOADS_DIR", tmp_path / "downloads")
    a_moi = tmp_path / nom
    a_moi.write_bytes(octets)
    vod_id = dbmod.create_vod(conn, title="t", source_path=str(a_moi))
    dbmod.set_vod_error(conn, vod_id, "Transcription : GPU", "transcribing: boom")
    return vod_id


def test_a_locally_imported_ts_goes_back_to_the_remux_for_its_copy(conn, tmp_path,
                                                                   monkeypatch):
    """Un .ts importe ne se seeke pas : tout clip rendu dessus est desynchronise
    (mesure du 2026-09-20 : 3684 ms d'avance video sous un audio juste). L'etape
    lui taille une copie indexee dans le dossier de donnees sans toucher a son
    fichier, donc la reprise doit bien l'y renvoyer.

    La promesse reste tenable : ramener a remuxing ne rejoue ni la transcription
    (drapeau words_complete) ni l'extraction audio (le wav est deja la)."""
    vod_id = _import_local(conn, tmp_path, monkeypatch, "capture.ts", _ts_brut())
    assert resume_step(dbmod.get_vod(conn, vod_id)) == "remuxing"


def test_a_locally_imported_fragmented_mp4_keeps_its_late_resume_point(conn, tmp_path,
                                                                       monkeypatch):
    """Aucun remux fantome sur le fichier du user : un MP4 fragmente importe
    seeke juste (25 ms, mesure du 2026-09-20), l'etape le laissera donc
    tranquille — la reprise ne doit pas promettre une reparation qui n'aura pas
    lieu."""
    frag = _atome(b"ftyp", b"isom") + _atome(b"moov", _atome(b"mvex")) + _atome(b"moof")
    vod_id = _import_local(conn, tmp_path, monkeypatch, "capture.mp4", frag)
    assert resume_step(dbmod.get_vod(conn, vod_id)) == "transcribing"


def test_a_vanished_source_keeps_its_resume_point(conn, tmp_path, monkeypatch):
    """Source disparue : la reprise ne bouge pas. step_download rend la main sans
    rien faire tant que source_path est renseigne, donc revenir en arriere ne
    retelechargerait rien — et une transcription, elle, ne vit que du wav deja
    extrait. Les etapes qui lisent le fichier le reclament par leur precondition."""
    from clipforge import config

    monkeypatch.setattr(config, "DOWNLOADS_DIR", tmp_path / "downloads")
    vod_id = dbmod.create_vod(conn, title="t",
                              source_path=str(tmp_path / "downloads" / "partie.mp4"))
    dbmod.set_vod_error(conn, vod_id, "Transcription : GPU", "transcribing: boom")
    assert resume_step(dbmod.get_vod(conn, vod_id)) == "transcribing"


def test_a_source_set_aside_sends_the_resume_back_to_the_download(conn, tmp_path,
                                                                  monkeypatch):
    """D8, second temps : apres la mise de cote, source_path est vide et la ligne
    dit `remuxing:`. Sans cette borne la reprise repartirait du remux, qui n'a
    plus rien a remuxer, et le sondage remourrait derriere — la boucle de D1 sous
    un autre nom."""
    from clipforge import config

    monkeypatch.setattr(config, "DOWNLOADS_DIR", tmp_path / "downloads")
    vod_id = dbmod.create_vod(conn, title="VOD 2836283171",
                              twitch_url="https://www.twitch.tv/videos/2836283171")
    dbmod.set_vod_error(conn, vod_id, "Indexation vidéo : Source endommagée",
                        "remuxing: source ecartee sous v2836283171.corrompu.mp4")
    vod = dbmod.get_vod(conn, vod_id)
    assert vod["source_path"] == ""
    assert resume_step(vod) == "downloading"

    calls = []
    steps = [(name, lambda c, v, ctx, n=name: calls.append(n)) for name in STEP_ORDER]
    run_steps(conn, vod_id, steps, ctx={}, resume_from=resume_step(vod))
    assert calls[:3] == ["downloading", "remuxing", "probing"]
    assert dbmod.get_vod(conn, vod_id)["status"] == "ready"


def test_a_vanished_download_is_fetched_again_however_late_the_step(conn, tmp_path,
                                                                    monkeypatch):
    """Fichier efface sous une VOD Twitch : la reprise repart du telechargement,
    quelle que soit l'etape que la ligne annonce. Aucune etape en aval ne sait
    travailler sans lui, et celle-la, elle, peut le ramener."""
    from clipforge import config

    monkeypatch.setattr(config, "DOWNLOADS_DIR", tmp_path / "downloads")
    for etape in ("remuxing", "probing", "transcribing", "rendering_clips"):
        vod_id = dbmod.create_vod(
            conn, title="t", source_path=str(tmp_path / "downloads" / "partie.mp4"),
            twitch_url="https://www.twitch.tv/videos/1")
        dbmod.set_vod_error(conn, vod_id, "Erreur", f"{etape}: boom")
        assert resume_step(dbmod.get_vod(conn, vod_id)) == "downloading"


@pytest.mark.integration
def test_the_real_poisoned_row_heals_and_gets_past_probing(conn, tmp_path, monkeypatch):
    """Bout en bout sur un VRAI MPEG-TS : la ligne du user doit guerir PAR L'APP.

    Trois etapes reelles suffisent a le prouver — telechargement (sans reseau : la
    source est deja la), remux, sondage. Le sondage mourait ici en une seconde ;
    il doit maintenant rendre une duree, ce qui vaut preuve que le conteneur a
    bien ete reecrit entre les deux.
    """
    from clipforge import config

    monkeypatch.setattr(config, "JOBS_DIR", tmp_path / "jobs")
    vod_id = _ligne_du_user(conn, tmp_path, monkeypatch, octets=b"",
                            etape_en_erreur="probing")
    source = Path(dbmod.get_vod(conn, vod_id)["source_path"])
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(FIXTURE), "-t", "5",
                    "-c", "copy", "-f", "mpegts", str(source)], check=True)
    conn.execute("UPDATE vods SET duration_s=? WHERE id=?", (5.0, vod_id))
    conn.commit()
    assert remux.is_mpegts(source) is True

    reprise = resume_step(dbmod.get_vod(conn, vod_id))
    assert reprise == "remuxing"
    reelles = dict(build_real_steps())
    run_steps(conn, vod_id, [(n, reelles[n]) for n in ("downloading", "remuxing", "probing")],
              ctx={}, resume_from=reprise)

    guerie = dbmod.get_vod(conn, vod_id)
    assert guerie["status"] == "ready", guerie["error"]
    assert remux.is_mpegts(source) is False        # le conteneur a ete reecrit
    assert guerie["duration_s"] == pytest.approx(5.0, abs=1.0)   # le sondage a abouti
    assert guerie["width"] and guerie["height"]


def test_segment_step_needs_no_word_to_produce_candidates(conn, monkeypatch, tmp_path):
    """Une VOD sans parole reste decoupable : les signaux audio et chat suffisent.

    narrative_bounds cale alors les bornes sur le pic au lieu des phrases. C'est
    ce qui rend le decoupage jouable apres une transcription vide (micro sur la
    mauvaise entree, sequence musicale) — et donc ce qui interdit d'exiger des
    mots pour demarrer l'etape.
    """
    from clipforge import config

    monkeypatch.setattr(config, "JOBS_DIR", tmp_path)
    vod_id = dbmod.create_vod(conn, title="t", source_path="x.mp4")
    conn.execute("UPDATE vods SET duration_s=600 WHERE id=?", (vod_id,))
    for t in range(0, 600, 5):
        sc = 0.95 if 95 <= t <= 105 else 0.1
        conn.execute("INSERT INTO signals VALUES (?,?,?,?)", (vod_id, "audio", float(t), sc))
        conn.execute("INSERT INTO signals VALUES (?,?,?,?)", (vod_id, "chat", float(t), sc))
    conn.commit()
    dict(build_real_steps())["segmenting"](conn, vod_id, {})
    cands = conn.execute("SELECT start_s, end_s, top_signal FROM segments WHERE vod_id=?",
                         (vod_id,)).fetchall()
    assert cands
    assert all(c["end_s"] > c["start_s"] for c in cands)
    assert cands[0]["top_signal"] in ("audio", "chat")
