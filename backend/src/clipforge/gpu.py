import os
import shutil
import subprocess
import sys
import threading
from collections.abc import MutableMapping
from pathlib import Path

# os.environ["PATH"] = ... + os.environ["PATH"] est un lire-modifier-ecrire :
# deux appelants simultanes peuvent perdre la modification de l'autre ou publier
# un PATH tronque. Le warmup GPU tourne dans un thread pendant que le pipeline
# appelle la meme fonction, donc le cas n'est pas theorique.
_path_lock = threading.Lock()

# os.add_dll_directory ne retire jamais son entree : l'objet rendu n'a pas de
# __del__ (CPython, Lib/os.py) et la reference est jetee ici. Sans ce registre,
# chaque transcription empilerait un doublon dans le chemin de recherche DLL.
_dll_lock = threading.Lock()
_registered_dll_dirs: set[str] = set()


def prepend_to_path(directory: str, env: MutableMapping[str, str] | None = None) -> None:
    """Ajoute un dossier en tete du PATH, une seule fois, sans course.

    La presence se teste entree par entree : en sous-chaine, un voisin plus long
    comme C:\\cuda\\bin_old ferait passer C:\\cuda\\bin pour deja enregistre.

    env permet aux tests de travailler sur un dictionnaire a eux : remplacer le
    PATH reel du process pendant qu'un thread de warmup lit la meme variable
    laisse un PATH ampute derriere soi.
    """
    target = os.environ if env is None else env
    with _path_lock:
        current = target.get("PATH", "")
        entries = {e.casefold() for e in current.split(os.pathsep) if e}
        if directory.casefold() not in entries:
            target["PATH"] = directory + os.pathsep + current


def _add_dll_directory_once(directory: str) -> None:
    with _dll_lock:
        if directory.casefold() in _registered_dll_dirs:
            return
        os.add_dll_directory(directory)
        _registered_dll_dirs.add(directory.casefold())


def register_cuda_dlls() -> None:
    """A appeler AVANT tout import de faster_whisper, cote process enfant.

    Idempotente et sans course : le pipeline l'appelle a chaque transcription
    pendant que le thread de prechauffage fait de meme.
    """
    if sys.platform != "win32":
        return
    try:
        import nvidia
    except ImportError:
        return
    for base in map(Path, nvidia.__path__):
        for sub in ("cublas", "cudnn"):
            dll_dir = base / sub / "bin"
            if dll_dir.is_dir():
                _add_dll_directory_once(str(dll_dir))
                # ctranslate2 charge cublas/cudnn via le chemin classique (PATH),
                # que add_dll_directory ne couvre pas
                prepend_to_path(str(dll_dir))


class VramInsuffisante(RuntimeError):
    """La carte visee n'a pas la place pour le modele demande."""


def vram_libre_gb(gpu_index: int, run=None) -> float | None:
    """VRAM libre de la carte `gpu_index` en GiB, None si nvidia-smi est absent.

    Sous Windows/WDDM, NVML ne sait pas attribuer la memoire par process, mais
    la memoire libre par carte est fiable : c'est la seule mesure utilisee.
    `run` est injectable pour les tests (signature de subprocess.run).
    """
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    runner = subprocess.run if run is None else run
    try:
        out = runner([exe, "-i", str(gpu_index), "--query-gpu=memory.free",
                      "--format=csv,noheader,nounits"],
                     capture_output=True, text=True, timeout=10, check=True)
    except (OSError, subprocess.SubprocessError):
        return None
    premiere = out.stdout.strip().splitlines()[:1]
    if not premiere:
        return None
    try:
        return int(premiere[0].strip()) / 1024
    except ValueError:
        return None


def verifier_vram(gpu_index: int, besoin_gb: float, run=None) -> float | None:
    """Refuse AVANT de charger un modele qui ne tiendra pas.

    Rend la VRAM libre mesuree (ou None si non mesurable : on laisse faire, le
    constructeur du modele dira lui-meme s'il echoue). Leve VramInsuffisante
    avec les chiffres, jamais un echec muet plus tard dans l'inference.
    """
    libre = vram_libre_gb(gpu_index, run=run)
    if libre is not None and besoin_gb > libre:
        raise VramInsuffisante(
            f"cuda:{gpu_index} : {libre:.1f} GiB libres, {besoin_gb:.1f} GiB requis")
    return libre

