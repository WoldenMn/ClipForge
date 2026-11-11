import os
import sys
import threading
import types

import pytest

from clipforge import gpu
from clipforge.gpu import prepend_to_path


def _dossier(nom: str) -> str:
    """Chemin d'essai valide sur la plateforme courante.

    Un chemin Windows en dur casse sur la CI Linux : os.pathsep y vaut « : »,
    donc « C:\\cuda\\bin » se scinde en « C » et « \\cuda\\bin » des le premier
    split. Le defaut etait dans le TEST, pas dans prepend_to_path — qui, elle,
    decoupe correctement avec le separateur de sa plateforme.
    """
    return f"C:\\{nom}" if os.pathsep == ";" else f"/{nom}"


def test_prepend_adds_the_directory_once():
    cuda, existant = _dossier("cuda\\bin"), _dossier("existant")
    env = {"PATH": existant}
    prepend_to_path(cuda, env)
    prepend_to_path(cuda, env)
    assert env["PATH"].count(cuda) == 1
    assert env["PATH"].endswith(existant)


def test_a_longer_neighbour_does_not_hide_the_target_directory():
    """La presence se teste entree par entree, pas en sous-chaine : sinon
    un voisin plus long ferait passer le dossier vise pour deja enregistre."""
    cuda = _dossier("cuda\\bin")
    env = {"PATH": f"{cuda}_old"}
    prepend_to_path(cuda, env)
    assert env["PATH"].split(os.pathsep)[0] == cuda


@pytest.mark.skipif(sys.platform != "win32", reason="add_dll_directory n'existe que sur Windows")
def test_registering_twice_adds_each_dll_directory_only_once(monkeypatch, tmp_path):
    """os.add_dll_directory ne retire jamais son entree.

    L'objet rendu n'a pas de __del__ (CPython Lib/os.py) et la reference est
    jetee : sans garde, chaque transcription empilait un doublon de plus dans
    le chemin de recherche DLL du process.
    """
    faux_nvidia = types.ModuleType("nvidia")
    faux_nvidia.__path__ = [str(tmp_path)]
    for sub in ("cublas", "cudnn"):
        (tmp_path / sub / "bin").mkdir(parents=True)
    monkeypatch.setitem(sys.modules, "nvidia", faux_nvidia)
    # registre neuf : le pipeline a deja pu enregistrer les vrais dossiers CUDA
    monkeypatch.setattr(gpu, "_registered_dll_dirs", set())
    monkeypatch.setenv("PATH", "base")
    enregistres = []
    monkeypatch.setattr(os, "add_dll_directory", enregistres.append)

    gpu.register_cuda_dlls()
    gpu.register_cuda_dlls()

    attendus = [str(tmp_path / sub / "bin") for sub in ("cublas", "cudnn")]
    assert sorted(enregistres) == sorted(attendus)
    for dossier in attendus:
        assert os.environ["PATH"].split(os.pathsep).count(dossier) == 1


@pytest.fixture
def ordonnanceur_hostile():
    """Changements de thread tres frequents : sans ca le GIL masque la course.

    Mesure a l'appui sur la version non protegee : 0 perte en cadence normale,
    524 entrees perdues sur 30 essais avec cet intervalle.
    """
    precedent = sys.getswitchinterval()
    sys.setswitchinterval(1e-9)
    yield
    sys.setswitchinterval(precedent)


def test_concurrent_calls_never_lose_a_directory(ordonnanceur_hostile):
    """Le lire-modifier-ecrire sur PATH perdait des entrees quand le warmup GPU
    et le pipeline enregistraient leurs DLL en meme temps.

    L'environnement est un dictionnaire local : toucher au PATH du process
    pendant que le thread de warmup le lit laisserait la suite sans ffmpeg.
    """
    env = {"PATH": "base"}
    dossiers = [f"D{i}" for i in range(60)]
    depart = threading.Barrier(len(dossiers))

    def ajoute(d):
        depart.wait()
        prepend_to_path(d, env)

    fils = [threading.Thread(target=ajoute, args=(d,)) for d in dossiers]
    for f in fils:
        f.start()
    for f in fils:
        f.join()

    perdus = [d for d in dossiers if d not in env["PATH"]]
    assert not perdus, f"{len(perdus)} entrees perdues : {perdus[:5]}"
    assert env["PATH"].endswith("base")
    assert os.environ["PATH"] != env["PATH"]  # le PATH du process est reste intact
