"""Lier la vie des processus enfants a celle du moteur (Windows).

Le moteur lance des enfants qui lui survivent : ffmpeg (rendu, remux, vignettes,
apercus) et les workers du pool. Un `finally`, un `atexit` ou un `shutdown` du
pool ne couvrent QUE la sortie polie — pas le crash, pas le kill du gestionnaire
des taches, pas la fermeture brutale de la fenetre Tauri. Ce qui restait alors
etait un ffmpeg orphelin travaillant pour un moteur qui n'existe plus, sur un
fichier que plus personne n'attend.

Un Job Object avec JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE deplace le menage dans le
noyau : la regle se declenche a la FERMETURE DU HANDLE, et Windows ferme les
handles d'un processus quelle que soit la facon dont il meurt. Le moteur n'a
donc rien a executer pour que ses enfants tombent avec lui.

Les enfants heritent de l'appartenance au job sans rien faire : tout processus
cree par un membre du job en devient membre. Rien a passer a subprocess, rien a
changer dans le pool.

Hors Windows, tout ce module est un no-op assume : la primitive n'existe pas et
le moteur n'est livre que sur Windows (CLAUDE.md).
"""
# Annotations paresseuses OBLIGATOIRES : `-> ctypes.WinDLL` dans une signature
# serait evalue a l'import du module, et ctypes n'a pas WinDLL sous Linux —
# la CI entiere tombait a l'import de main.py.
from __future__ import annotations

import ctypes
import logging
import sys

logger = logging.getLogger(__name__)

JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
JOBOBJECT_EXTENDED_LIMIT_INFORMATION_CLASS = 9

# Le handle du job vit ici, en global, et n'est JAMAIS ferme : le fermer tuerait
# le moteur lui-meme, qui est membre de son propre job. Sa duree de vie est celle
# du processus, et c'est le systeme qui le ferme en dernier, quand il n'y a plus
# personne a tuer. Un handle non heritable (attributs de securite par defaut) :
# si les enfants en recevaient une copie, tuer le moteur ne fermerait pas le
# dernier handle et la regle ne se declencherait pas.
_job: int | None = None


class _IO_COUNTERS(ctypes.Structure):
    _fields_ = [("ReadOperationCount", ctypes.c_ulonglong),
                ("WriteOperationCount", ctypes.c_ulonglong),
                ("OtherOperationCount", ctypes.c_ulonglong),
                ("ReadTransferCount", ctypes.c_ulonglong),
                ("WriteTransferCount", ctypes.c_ulonglong),
                ("OtherTransferCount", ctypes.c_ulonglong)]


class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", ctypes.c_uint32),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", ctypes.c_uint32),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", ctypes.c_uint32),
                ("SchedulingClass", ctypes.c_uint32)]


class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ("IoInfo", _IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t)]


def _kernel32() -> ctypes.WinDLL:
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateJobObjectW.restype = ctypes.c_void_p
    k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
    k32.SetInformationJobObject.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                            ctypes.c_void_p, ctypes.c_uint32]
    k32.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    k32.GetCurrentProcess.restype = ctypes.c_void_p
    k32.CloseHandle.argtypes = [ctypes.c_void_p]
    return k32


def _echec(k32: ctypes.WinDLL, appel: str) -> OSError:
    code = ctypes.get_last_error()
    return OSError(0, f"{appel} a echoue", None, code)


def bind_children_to_engine() -> bool:
    """Assigne le processus courant a un job qui tuera ses enfants avec lui.

    True si le lien est en place, False sinon — et JAMAIS d'exception : un moteur
    qui refuse de demarrer parce qu'il n'a pas pu poser un filet de securite
    serait un remede pire que le mal. Le cas connu est un moteur deja place dans
    un job qui interdit l'imbrication : Windows 8 et suivants imbriquent les jobs,
    mais un job exterieur peut fermer la porte. L'echec est alors consigne et le
    moteur tourne comme avant, avec le risque d'orphelin qu'il a toujours eu.

    Idempotent : le second appel rend True sans creer de job. Sans cette garde,
    chaque relance du lifespan (les tests d'API le rejouent des dizaines de fois
    dans un meme processus) empilerait un job de plus, jusqu'a la limite
    d'imbrication.
    """
    global _job
    if sys.platform != "win32":
        return False
    if _job is not None:
        return True
    k32 = _kernel32()
    job = None
    try:
        job = k32.CreateJobObjectW(None, None)
        if not job:
            raise _echec(k32, "CreateJobObjectW")
        info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not k32.SetInformationJobObject(job, JOBOBJECT_EXTENDED_LIMIT_INFORMATION_CLASS,
                                           ctypes.byref(info), ctypes.sizeof(info)):
            raise _echec(k32, "SetInformationJobObject")
        if not k32.AssignProcessToJobObject(job, k32.GetCurrentProcess()):
            raise _echec(k32, "AssignProcessToJobObject")
    except OSError as exc:
        if job:
            # Le job est vide (l'assignation a echoue) : le fermer ne tue personne.
            k32.CloseHandle(job)
        logger.warning("Enfants non lies a la vie du moteur (%s) : un ffmpeg tue en "
                       "cours de route pourra survivre au moteur", exc)
        return False
    _job = job
    logger.info("Enfants lies a la vie du moteur (Job Object, kill on close)")
    return True
