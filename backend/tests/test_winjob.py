"""Les enfants du moteur meurent-ils avec lui, quelle que soit la facon dont il meurt ?

Le sujet est un vrai arbre de processus, pas un mock : un « moteur » factice est
lance, il engendre un enfant, puis il est TUE (TerminateProcess, comme un crash
— aucun code de sortie propre ne s'execute). Reste a savoir si l'enfant survit.

Les deux tests forment une paire, et le premier est le temoin CONNU-MAUVAIS qui
donne sa valeur au second : sans job, l'enfant doit survivre. S'il mourait aussi
dans ce cas-la, la sonde ne mesurerait rien (un enfant qui s'arrete tout seul
donnerait le meme vert que le job).

Win32 uniquement — un Job Object est une primitive Windows — et deliberement
SANS marqueur `integration` : aucun ffmpeg ici, rien que python et un sleep.
"""
import ctypes
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32",
                                reason="Job Object : primitive Windows")

# L'echec d'assignation n'interrompt PAS le moteur factice : ce que les tests
# mesurent est le sort de l'enfant, pas la valeur rendue par la fonction. Un
# moteur reel non plus ne s'arrete pas pour si peu (main.py).
MOTEUR = """
import subprocess, sys, time

if sys.argv[1] == "avec-job":
    from clipforge.winjob import bind_children_to_engine
    bind_children_to_engine()
enfant = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
print(enfant.pid, flush=True)
time.sleep(120)
"""

DELAI_MORT_S = 5.0
PAS_S = 0.1
STILL_ACTIVE = 259
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def _vivant(pid: int) -> bool:
    """Le processus tourne-t-il encore ? OpenProcess + code de sortie.

    Surtout PAS os.kill(pid, 0) : sur Windows, os.kill appelle TerminateProcess
    quel que soit le signal — la sonde tuerait l'enfant qu'elle observe et le
    temoin rendrait un faux « mort ».
    """
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.restype = ctypes.c_void_p
    k32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    k32.CloseHandle.argtypes = [ctypes.c_void_p]
    k32.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
    handle = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return False
    try:
        code = ctypes.c_ulong()
        if not k32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return False
        return code.value == STILL_ACTIVE
    finally:
        k32.CloseHandle(handle)


def _tuer(pid: int) -> None:
    subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True, check=False)


def _enfant_survit_a_la_mort_du_moteur(tmp_path: Path, *, avec_job: bool) -> bool:
    """Lance un moteur factice, tue-le net, dit si son enfant est toujours la."""
    script = tmp_path / "faux_moteur.py"
    script.write_text(MOTEUR, encoding="utf-8")
    moteur = subprocess.Popen(
        [sys.executable, str(script), "avec-job" if avec_job else "sans-job"],
        stdout=subprocess.PIPE, text=True, encoding="utf-8")
    enfant_pid = None
    try:
        assert moteur.stdout is not None
        ligne = moteur.stdout.readline().strip()
        assert ligne.isdigit(), f"moteur factice sans enfant : {ligne!r}"
        enfant_pid = int(ligne)
        assert _vivant(enfant_pid), "l'enfant n'a jamais demarre : rien a mesurer"
        moteur.kill()                       # TerminateProcess : la mort brutale, pas l'arret propre
        moteur.wait(timeout=DELAI_MORT_S)
        fin = time.monotonic() + DELAI_MORT_S
        while time.monotonic() < fin:
            if not _vivant(enfant_pid):
                return False
            time.sleep(PAS_S)
        return True
    finally:
        moteur.kill()
        if enfant_pid is not None:
            _tuer(enfant_pid)


def test_a_child_survives_the_engine_without_the_job(tmp_path):
    """TEMOIN connu-mauvais : c'est l'orphelin de D7, mesure et non suppose.

    Un enfant lance sans precaution n'a AUCUN lien avec la vie de son parent sur
    Windows : tuer le moteur le laisse tourner. C'est ce test qui prouve que la
    sonde du test suivant regarde vraiment quelque chose.
    """
    assert _enfant_survit_a_la_mort_du_moteur(tmp_path, avec_job=False) is True


def test_the_job_reaps_every_child_when_the_engine_dies(tmp_path):
    """Le meme moteur, assigne a un Job Object : Windows fait le menage seul.

    KILL_ON_JOB_CLOSE porte sur la FERMETURE du handle, pas sur un arret propre :
    c'est ce qui couvre le crash, le kill et la coupure de courant logicielle, la
    ou un atexit ou un finally ne couvrent que la sortie polie.
    """
    assert _enfant_survit_a_la_mort_du_moteur(tmp_path, avec_job=True) is False

