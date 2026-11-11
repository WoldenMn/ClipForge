"""La collecte machine DECRIT, y compris ses propres pannes : elle ne leve pas.

Contrat de confidentialite : aucune valeur d'environnement ne sort d'ici. La cle
API est signalee presente ou absente, jamais lue.
"""
import shutil
import subprocess

import pytest

from clipforge import db as dbmod
from clipforge import diagnostic, usage

SENTINELLE = "valeur-sentinelle-qui-ne-doit-jamais-sortir-42"


def _faux_outils(monkeypatch):
    """Tous les outils resolus : les sondes vont jusqu'a l'appel systeme."""
    monkeypatch.setattr("shutil.which", lambda nom: rf"C:\faux\{nom}.exe")


def test_machine_state_reports_the_tools_it_finds():
    etat = diagnostic.machine_state()
    assert set(etat) >= {"ffmpeg", "gpu", "disque_libre_go", "memoire_libre_go",
                         "python", "anthropic_key"}
    assert isinstance(etat["disque_libre_go"], float)


def test_a_missing_tool_is_reported_not_raised(monkeypatch):
    """Un ffmpeg absent est une INFORMATION de diagnostic, pas une panne."""
    monkeypatch.setattr("shutil.which", lambda _: None)
    etat = diagnostic.machine_state()
    assert etat["ffmpeg"] == "introuvable"


def test_a_probe_that_crashes_is_described_not_raised(monkeypatch):
    """Le rapport se demande justement quand la machine va mal : une sonde qui
    tombe doit rendre sa raison, pas emporter le rapport entier."""
    _faux_outils(monkeypatch)

    def boum(*_args, **_kwargs):
        raise OSError("le processus n'a pas pu demarrer")

    monkeypatch.setattr("clipforge.diagnostic.subprocess.run", boum)
    etat = diagnostic.machine_state()
    assert "le processus n'a pas pu demarrer" in etat["ffmpeg"]
    assert "le processus n'a pas pu demarrer" in etat["gpu"]


def test_a_tool_that_answers_with_an_error_is_not_taken_for_a_result(monkeypatch):
    """nvidia-smi refuse un argument SUR SA SORTIE STANDARD, avec le code 2.

    Lire stdout sans regarder le code de retour ferait passer « ERROR: Option
    ... is not recognized » pour la description du GPU, et le controle de sante
    de la phase 2 pour un GPU parfaitement detecte.
    """
    _faux_outils(monkeypatch)
    monkeypatch.setattr(
        "clipforge.diagnostic.subprocess.run",
        lambda args, **_kwargs: subprocess.CompletedProcess(
            args, 2, "ERROR: Option --csv,noheader is not recognized.", ""))
    etat = diagnostic.machine_state()
    assert "code 2" in etat["gpu"]
    assert "code 2" in etat["ffmpeg"]


@pytest.mark.skipif(shutil.which("nvidia-smi") is None,
                    reason="aucun nvidia-smi sur cette machine")
def test_the_gpu_query_uses_arguments_that_nvidia_smi_accepts():
    """Garde de regression sur la forme des arguments, verifiee contre le vrai
    binaire : `--csv,noheader` est refuse, seul `--<clef>=csv,noheader` passe."""
    gpu = diagnostic.machine_state()["gpu"]
    for echec in ("introuvable", "impossible", "code "):
        assert echec not in gpu


def _etat_avec(monkeypatch, run):
    _faux_outils(monkeypatch)
    monkeypatch.setattr("clipforge.diagnostic.subprocess.run", run)
    return diagnostic.machine_state()


def test_every_probe_failure_is_recognised_as_one(monkeypatch):
    """health.py lit ces chaines pour decider si un outil repond.

    Une explication d'echec que `sonde_en_echec` ne reconnaitrait pas passerait
    pour un resultat : la banniere annoncerait un GPU detecte la ou nvidia-smi
    vient de refuser, et le user lancerait 2 h de transcription la-dessus.
    """
    monkeypatch.setattr("shutil.which", lambda _: None)
    absent = diagnostic.machine_state()
    assert diagnostic.sonde_en_echec(absent["ffmpeg"])
    assert diagnostic.sonde_en_echec(absent["gpu"])

    def boum(*_args, **_kwargs):
        raise OSError("le processus n'a pas pu demarrer")

    def refus(args, **_kwargs):
        return subprocess.CompletedProcess(args, 2, "ERROR: option refusee", "")

    def muet(args, **_kwargs):
        return subprocess.CompletedProcess(args, 0, "", "")

    for run in (boum, refus, muet):
        etat = _etat_avec(monkeypatch, run)
        assert diagnostic.sonde_en_echec(etat["ffmpeg"]), etat["ffmpeg"]
        assert diagnostic.sonde_en_echec(etat["gpu"]), etat["gpu"]


def test_a_real_answer_is_never_taken_for_a_failure(monkeypatch):
    """L'inverse compte autant : une banniere qui crie sur une machine saine
    apprend a la fermer sans lire."""
    def repond(args, **_kwargs):
        return subprocess.CompletedProcess(args, 0, "ffmpeg version 9.0-full_build", "")

    etat = _etat_avec(monkeypatch, repond)
    assert not diagnostic.sonde_en_echec(etat["ffmpeg"])
    assert not diagnostic.sonde_en_echec(etat["gpu"])


@pytest.mark.parametrize("ligne, verdict", [
    ("ffmpeg version 9.0.1-full_build-www.gyan.dev Copyright (c) 2000-2026", "ok"),
    ("ffmpeg version n7.0.2 Copyright (c) 2000-2024", "ok"),
    ("ffmpeg version 6.1.1-essentials_build-www.gyan.dev Copyright (c) 2000-2023", "6.1"),
    ("ffmpeg version N-116297-g4f1b2c3d-20240701 Copyright (c) 2000-2024", "non reconnue"),
])
def test_an_ffmpeg_older_than_7_is_flagged_for_the_v6_montage(monkeypatch, ligne, verdict):
    """Revue A2, fix round 2 : le montage V6 passe son graphe par
    -/filter_complex, option apparue avec ffmpeg 7.0. Plus ancien, chaque rendu
    V6 echouerait : le rapport le dit en clair, comme une version illisible."""
    etat = _etat_avec(monkeypatch, lambda args, **_kwargs: subprocess.CompletedProcess(
        args, 0, ligne, ""))
    montage = etat["ffmpeg_montage_v6"]
    if verdict == "ok":
        assert montage == "ok", montage
    else:
        assert verdict in montage and "7.0" in montage, montage


def test_a_missing_ffmpeg_is_reported_for_the_v6_montage_too(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda _: None)
    assert diagnostic.machine_state()["ffmpeg_montage_v6"] == "introuvable"


def test_the_api_key_value_never_appears(monkeypatch):
    """Contrat de confidentialite : on note la PRESENCE, jamais la valeur."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", f"sk-ant-{SENTINELLE}")
    etat = diagnostic.machine_state()
    assert etat["anthropic_key"] == "presente"
    assert SENTINELLE not in repr(etat)


def test_no_secret_looking_variable_is_ever_copied(monkeypatch):
    """Le contrat ne vaut pas que pour la cle Claude : rien de l'environnement
    n'est recopie, donc aucun secret ne peut suivre le rapport."""
    for nom in ("ANTHROPIC_API_KEY", "CLIPFORGE_TOKEN",
                "AWS_SECRET_ACCESS_KEY", "GH_PASSWORD"):
        monkeypatch.setenv(nom, SENTINELLE)
    assert SENTINELLE not in repr(diagnostic.machine_state())


def test_an_absent_key_is_reported_absent(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert diagnostic.machine_state()["anthropic_key"] == "absente"


def test_the_report_shows_no_raw_none(conn):
    """Un rapport constelle de « None » se lit comme un logiciel casse. Une VOD
    jamais sondee a une definition INCONNUE, elle ne mesure pas « NonexNone »."""
    vod_id = dbmod.create_vod(conn, title="jamais analysee", source_path="x.mp4")
    usage.record(conn, "bounds_adjusted", vod_id=vod_id,
                 start_shift_s=-4.0, end_shift_s=0.0)
    assert "None" not in diagnostic.build_report(conn)


def test_the_report_keeps_the_end_of_a_long_trace(conn):
    """runner.py garde deja la FIN de la trace ([-2000:]) : l'exception est la.
    Couper la tete de ce qui est deja une fin retirerait du rapport la seule
    ligne qui nomme l'erreur."""
    vod_id = dbmod.create_vod(conn, title="t", source_path="x.mp4")
    dbmod.set_vod_error(conn, vod_id, "Analyse : echec",
                        "bruit\n" * 900 + "ValueError: la vraie cause")
    assert "ValueError: la vraie cause" in diagnostic.build_report(conn)
