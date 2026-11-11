"""Le controle de sante existe pour EPARGNER une analyse qui allait echouer.

D'ou la regle qui gouverne ce module : un controle qui echoue porte SON REMEDE.
Un constat sans action a faire est ce que la V3 affichait deja, et qui n'aidait
personne.
"""
import importlib
import subprocess

import pytest
from fastapi.testclient import TestClient

from clipforge import config, health


def _controle(controles: list[dict], cle: str) -> dict:
    return next(c for c in controles if c["cle"] == cle)


def _outils_absents(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda _: None)


def _outils_presents(monkeypatch):
    """Tous les outils resolus : les sondes vont jusqu'a l'appel systeme."""
    monkeypatch.setattr("shutil.which", lambda nom: rf"C:\faux\{nom}.exe")


def _machine_saine(monkeypatch):
    """Outils resolus ET qui repondent : la machine telle qu'elle doit etre."""
    _outils_presents(monkeypatch)
    monkeypatch.setattr(
        "clipforge.diagnostic.subprocess.run",
        lambda args, **_kwargs: subprocess.CompletedProcess(
            args, 0, "NVIDIA GeForce RTX 4080, 16376 MiB", ""))


def test_every_failing_check_carries_its_remedy(monkeypatch):
    _outils_absents(monkeypatch)
    echecs = [c for c in health.run_checks_without_db() if not c["ok"]]
    assert echecs, "aucun controle ne tombe alors que plus rien n'est installe"
    for controle in echecs:
        assert controle["action"], f"{controle['cle']} constate sans rien a faire"


def test_a_check_that_passes_carries_no_action(monkeypatch):
    """L'action est le remede d'un echec : la porter sur un succes remplirait la
    banniere de conseils pour des choses qui vont bien."""
    _machine_saine(monkeypatch)
    ffmpeg = _controle(health.run_checks_without_db(), "ffmpeg")
    assert ffmpeg["ok"] is True
    assert ffmpeg["action"] == ""


def test_a_missing_ffmpeg_names_the_command_that_installs_it(monkeypatch):
    _outils_absents(monkeypatch)
    ffmpeg = _controle(health.run_checks_without_db(), "ffmpeg")
    assert ffmpeg["ok"] is False
    assert "winget install" in ffmpeg["action"]


def test_an_ffmpeg_that_resolves_but_never_runs_is_not_taken_for_installed(monkeypatch):
    """Une entree de PATH vers un dossier versionne survit a la disparition du
    dossier : `which` la trouve encore, l'appel echoue quarante minutes plus
    tard au milieu d'une analyse. Le remede n'est alors plus le meme.
    """
    _outils_presents(monkeypatch)  # resolu, mais C:\faux\ n'existe pas
    ffmpeg = _controle(health.run_checks_without_db(), "ffmpeg")
    assert ffmpeg["ok"] is False
    assert r"C:\faux\ffmpeg.exe" in ffmpeg["action"]
    assert "winget install" not in ffmpeg["action"]


def test_a_missing_ffprobe_is_not_hidden_by_a_present_ffmpeg(monkeypatch):
    """Le pipeline resout les deux binaires SEPAREMENT : step_probe appelle
    ffprobe (pipeline/probe.py) la ou le remux et les rendus appellent ffmpeg.

    Un ffmpeg.exe tiers deja dans le PATH (OBS en installe un) sans ffprobe a
    cote laisse la banniere au vert : les 9 Go se telechargent, le remux reecrit
    le fichier, et l'analyse tombe a l'etape 3 sur 8 sur « ffprobe introuvable ».
    """
    _machine_saine(monkeypatch)
    monkeypatch.setattr(
        "shutil.which", lambda nom: None if nom == "ffprobe" else rf"C:\faux\{nom}.exe")
    controles = health.run_checks_without_db()
    ffprobe = _controle(controles, "ffprobe")
    assert ffprobe["ok"] is False
    assert ffprobe["action"]
    assert _controle(controles, "ffmpeg")["ok"] is True


def test_both_binaries_pass_on_a_machine_where_they_answer(monkeypatch):
    """L'inverse du precedent : un controle qui crie sur une machine saine
    apprend a fermer la banniere sans la lire."""
    _machine_saine(monkeypatch)
    controles = health.run_checks_without_db()
    for cle in ("ffmpeg", "ffprobe"):
        assert _controle(controles, cle)["ok"] is True, cle


def test_a_gpu_answering_an_error_is_not_taken_for_a_detected_gpu(monkeypatch):
    """nvidia-smi refuse un argument SUR SA SORTIE STANDARD, avec le code 2.

    Prendre ce message pour une carte detectee produirait exactement le faux vert
    que ce controle doit empecher : le user lancerait une transcription de 2 h en
    croyant tenir son GPU.
    """
    _outils_presents(monkeypatch)
    monkeypatch.setattr(
        "clipforge.diagnostic.subprocess.run",
        lambda args, **_kwargs: subprocess.CompletedProcess(
            args, 2, "ERROR: Option --csv,noheader is not recognized.", ""))
    gpu = _controle(health.run_checks_without_db(), "gpu")
    assert gpu["ok"] is False
    assert gpu["action"]


def test_the_disk_check_names_the_directory_to_free(monkeypatch, tmp_path):
    """« Libere de la place » sans dire ou est un constat, pas un remede : le
    disque a liberer est celui qui porte les VOD, pas celui de l'utilisateur.

    Le dossier de donnees est pointe sur un tmp_path plutot que laisse a celui
    de la machine : sans ca, ce test ne parle du remede « libere de la place »
    que tant qu'un CLIPFORGE_DATA joignable traine dans l'environnement.
    """
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(health, "SEUIL_DISQUE_GO", 10.0**9)
    disque = _controle(health.run_checks_without_db(), "disque")
    assert disque["ok"] is False
    assert str(config.DATA_DIR) in disque["action"]


def test_an_unreachable_data_directory_is_described_not_raised(monkeypatch, tmp_path):
    """CLIPFORGE_DATA sur un disque externe debranche : la mesure ne peut plus
    se prendre. Lever ici emporte TOUS les controles, l'endpoint rend 500 et
    HealthBanner l'avale dans un console.warn : le controle de sante devient
    muet exactement quand l'environnement est casse. Il DECRIT, comme
    diagnostic.py, qui s'impose deja cette regle (diagnostic.py:5).
    """
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "jamais-cree")
    disque = _controle(health.run_checks_without_db(), "disque")
    assert disque["ok"] is False
    assert str(config.DATA_DIR) in disque["detail"]
    assert disque["action"]


def test_free_memory_is_checked_because_the_measure_is_already_in_hand(monkeypatch):
    """Un des trois symptomes que la spec cite nommement : une transcription qui
    echoue sur une allocation au bout de quarante minutes. La mesure etait deja
    dans machine_state(), le controle manquait."""
    _machine_saine(monkeypatch)
    monkeypatch.setattr("clipforge.diagnostic._memoire_libre_go", lambda: 1.2)
    memoire = _controle(health.run_checks_without_db(), "memoire")
    assert memoire["ok"] is False
    assert "1.2" in memoire["detail"]
    assert memoire["action"]


def test_enough_free_memory_passes_without_advice(monkeypatch):
    _machine_saine(monkeypatch)
    monkeypatch.setattr("clipforge.diagnostic._memoire_libre_go", lambda: 24.0)
    memoire = _controle(health.run_checks_without_db(), "memoire")
    assert memoire["ok"] is True
    assert memoire["action"] == ""


def test_a_memory_measure_that_failed_is_never_taken_for_zero(monkeypatch):
    """_memoire_libre_go rend un flottant OU une chaine explicative : Windows
    peut refuser la mesure. Prendre la chaine pour un zero crierait a la machine
    a l'agonie sur une machine parfaitement saine — et une banniere qui crie
    s'apprend a fermer sans lire. C'est un echec DECRIT, pas une alerte."""
    _machine_saine(monkeypatch)
    monkeypatch.setattr("clipforge.diagnostic._memoire_libre_go",
                        lambda: "mesure refusee par Windows")
    memoire = _controle(health.run_checks_without_db(), "memoire")
    assert memoire["ok"] is False
    assert "mesure refusee par Windows" in memoire["detail"]
    assert "0.0" not in memoire["detail"]
    assert "Go libres" not in memoire["detail"]
    assert memoire["action"]


def _ffmpeg_en_version(monkeypatch, ligne: str):
    """Machine saine dont `ffmpeg -version` rend `ligne` (nvidia-smi repond)."""
    _outils_presents(monkeypatch)

    def _repond(args, **_kwargs):
        sortie = ligne if "ffmpeg" in args[0] else "NVIDIA GeForce RTX 4080, 16376 MiB"
        return subprocess.CompletedProcess(args, 0, sortie, "")

    monkeypatch.setattr("clipforge.diagnostic.subprocess.run", _repond)
    return _controle(health.run_checks_without_db(), "ffmpeg_montage")


def test_an_ffmpeg_below_seven_fails_the_banner_with_the_upgrade_command(monkeypatch):
    """Le montage passe son graphe par -/filter_complex, option apparue avec
    ffmpeg 7.0 : plus ancien, TOUT rendu echoue sur « Error parsing global
    options », un message generique dont rien ne dit d'ou il vient. Le rapport
    de diagnostic le savait deja (ffmpeg_montage_v6) ; la banniere, non."""
    montage = _ffmpeg_en_version(
        monkeypatch, "ffmpeg version 6.1.1-essentials_build-www.gyan.dev Copyright")
    assert montage["ok"] is False
    assert "6.1" in montage["detail"]
    assert "winget upgrade Gyan.FFmpeg" in montage["action"]


@pytest.mark.parametrize("ligne", [
    "ffmpeg version n7.0.2 Copyright (c) 2000-2024",
    "ffmpeg version 9.0.1-full_build-www.gyan.dev Copyright (c) 2000-2026",
])
def test_an_ffmpeg_at_or_above_seven_passes_without_advice(monkeypatch, ligne):
    montage = _ffmpeg_en_version(monkeypatch, ligne)
    assert montage["ok"] is True, montage["detail"]
    assert montage["action"] == ""


def test_an_unreadable_ffmpeg_version_never_fails_the_montage_check(monkeypatch):
    """Un build de developpement date (gyan git) n'a pas de X.Y a lire, et il
    est le plus souvent PLUS recent que le plancher. Rougir dessus enverrait
    mettre a jour un ffmpeg deja a jour : la banniere apprendrait a se faire
    fermer sans etre lue. Le rapport de diagnostic porte la chaine exacte."""
    montage = _ffmpeg_en_version(
        monkeypatch, "ffmpeg version N-116297-g4f1b2c3d-20240701 Copyright")
    assert montage["ok"] is True
    assert "non reconnue" in montage["detail"]


def test_a_missing_ffmpeg_does_not_double_the_banner_line(monkeypatch):
    """ffmpeg introuvable : le controle "ffmpeg" est deja rouge avec son propre
    remede. Un second rouge pour la meme cause n'apprendrait rien et volerait la
    place du reste."""
    _outils_absents(monkeypatch)
    controles = health.run_checks_without_db()
    assert _controle(controles, "ffmpeg")["ok"] is False
    assert _controle(controles, "ffmpeg_montage")["ok"] is True


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CLIPFORGE_DATA", str(tmp_path))
    importlib.reload(config)
    from clipforge import main as mainmod
    importlib.reload(mainmod)
    with TestClient(mainmod.app, base_url="http://127.0.0.1:8765") as c:
        yield c


def test_the_endpoint_returns_the_machine_and_the_data_checks(client):
    controles = client.get("/api/health-checks").json()["controles"]
    assert {c["cle"] for c in controles} >= {"ffmpeg", "ffprobe", "ffmpeg_montage",
                                             "disque", "gpu", "memoire", "schema",
                                             "sources", "indexation"}


def test_the_endpoint_answers_even_when_the_data_directory_is_gone(
        client, monkeypatch, tmp_path):
    """Le symptome vu par le user : sans garde, la mesure d'espace libre leve
    au fond de l'endpoint. En production uvicorn rend 500 et la banniere se
    tait ; ici TestClient propage l'exception. Les deux disent la meme chose."""
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "debranche")
    reponse = client.get("/api/health-checks")
    assert reponse.status_code == 200
    assert _controle(reponse.json()["controles"], "disque")["ok"] is False


def test_the_tauri_probe_keeps_its_exact_body(client):
    """Tauri verifie ce corps a l'octet pres (engine_owner::is_engine_response) :
    le controle de sante passe par un autre chemin, jamais par celui-la."""
    reponse = client.get("/api/health")
    assert reponse.status_code == 200
    assert reponse.json() == {"ok": True}
