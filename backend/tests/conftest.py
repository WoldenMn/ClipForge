import importlib
import os
import subprocess
import sys
import warnings
from pathlib import Path

import garde_data
import pytest
from faux_ffmpeg import FauxFfmpeg

from clipforge import config
from clipforge import db as dbmod
from clipforge.render import montage_plan as montage_plan_mod

FIXTURE_MP4 = Path(__file__).parent / "fixtures" / "fixture.mp4"
MAKE_FIXTURE = Path(__file__).parent.parent / "scripts" / "make_fixture.py"
DONNEES_DU_DEPOT = config.PROJECT_ROOT / "data"
CHEMINS_DE_CONFIG = ("DATA_DIR", "DB_PATH", "JOBS_DIR", "OUTPUT_DIR", "DOWNLOADS_DIR",
                     "LOG_PATH")
_DONNEES_ABSENTES = pytest.StashKey[bool]()


def pytest_sessionstart(session):
    session.config.stash[_DONNEES_ABSENTES] = not DONNEES_DU_DEPOT.exists()
    garde_data.armer(DONNEES_DU_DEPOT)


def pytest_sessionfinish(session):
    garde_data.desarmer()


@pytest.fixture(scope="session", autouse=True)
def garde_donnees_du_depot(request):
    """Garde de <racine du depot>/data, le dossier du vrai moteur, en trois
    pieces, toutes armees avant la collecte.

    1. L'audit hook de garde_data.py : toute ecriture de CE processus sous
       data/ (ouverture en ecriture, mkdir, remove, rename, rmdir, rmtree,
       lien, troncature, connexion sqlite meme par URI file:) est refusee AVANT
       d'avoir lieu et consignee, qu'elle passe par config ou par un chemin en
       dur, pendant la collecte, un test ou une fixture de module, par un chemin
       court 8.3, un prefixe long ou dans une autre casse. Dans un test, ce test
       rougit (aucune_ecriture_dans_data) ; pendant la collecte, la collecte
       echoue ; ailleurs, la session rougit ici.
    2. Apres chaque test, aucun chemin de config ne pointe dans data/
       (config_hors_du_depot).
    3. data/ absent au demarrage et present a la fin : la session rougit ici,
       quel que soit l'auteur.

    Ce qui echappe. L'audit hook ne suit pas les processus enfants : data/
    present au demarrage (checkout principal, ou vivent la vraie base et les
    VOD), un sous-processus qui reimporte clipforge sans CLIPFORGE_DATA ecrit
    sans etre vu ; data/ absent (CI), il n'est vu que s'il laisse data/
    derriere lui (piece 3). Et il compare des chemins en texte : un lien ou
    une jonction qui mene dans data/ par un autre chemin que la racine du
    depot n'est pas suivi.

    Faux rouge restant (piece 3) : data/ absent au demarrage et cree pendant
    la session par le vrai moteur lance depuis le meme checkout -- premier
    lancement d'un checkout neuf seulement. Aucun parcours de l'arbre : le
    releve profond d'avant coutait pres de 11 s sur 20 000 entrees et
    accusait la suite des ecritures du moteur."""
    yield
    fautes = []
    if request.config.stash[_DONNEES_ABSENTES] and DONNEES_DU_DEPOT.exists():
        try:
            contenu = sorted(p.name for p in DONNEES_DU_DEPOT.iterdir())
        except OSError as exc:
            contenu = f"illisible : {exc}"
        fautes.append(f"la suite a cree {DONNEES_DU_DEPOT} : {contenu}")
    if garde_data.ecritures:
        fautes.append(f"ecritures sous data/ hors test : {garde_data.ecritures[:5]}")
    if fautes:
        pytest.fail(" ; ".join(fautes), pytrace=False)


@pytest.fixture(autouse=True)
def aucune_ecriture_dans_data():
    """Fait rougir LE test pendant lequel l'audit hook a refuse une ecriture
    sous data/ (piece 1 de garde_donnees_du_depot). Le refus leve deja dans le
    code fautif ; ce controle tient meme quand ce code avale l'exception."""
    deja = len(garde_data.ecritures)
    yield
    nouvelles = garde_data.ecritures[deja:]
    del garde_data.ecritures[deja:]
    if nouvelles:
        pytest.fail(f"ecriture refusee sous {DONNEES_DU_DEPOT} : {nouvelles[:5]}",
                    pytrace=False)


@pytest.fixture(autouse=True)
def config_hors_du_depot(monkeypatch):
    """Fait rougir LE test apres lequel un chemin de config pointe dans
    <racine du depot>/data (piece 2) : c'est la cause des accidents deja
    mesures, isolation retiree ou config repointe par une fixture. Ne juge que
    l'etat final ; une ecriture faite pendant le test, config restaure
    depuis, releve de l'audit hook.

    Demande `monkeypatch` pour passer AVANT son undo : un config modifie par
    le test est juge tel que le test l'a laisse."""
    yield
    fautifs = {nom: str(getattr(config, nom)) for nom in CHEMINS_DE_CONFIG
               if Path(os.path.abspath(getattr(config, nom))).is_relative_to(DONNEES_DU_DEPOT)}
    if fautifs:
        pytest.fail(f"config pointe dans {DONNEES_DU_DEPOT} : {fautifs}", pytrace=False)


@pytest.fixture(scope="session", autouse=True)
def donnees_hors_du_depot(garde_donnees_du_depot, tmp_path_factory):
    """Donnees et journal du moteur dans un dossier temporaire, toute la session.

    render_clip_job et le runner appellent config.setup_logging, qui ecrit
    config.LOG_PATH : <racine du depot>/data/moteur.log par defaut, fige a
    l'import. Un rendu d'integration lance sans CLIPFORGE_DATA ouvrait donc le
    journal du vrai moteur, y ajoutait ses avertissements, et creait data/ et
    moteur.log s'ils manquaient (un seul rendu du cas 3 suffit, mesure sur une
    copie propre du depot). Portee session : les fixtures de module
    (rendered_gap_clip...) rendent avant toute fixture de fonction. config est
    recharge comme le font les fixtures d'API, le vrai setup_logging tourne sur
    ce dossier-la."""
    ancien = os.environ.get("CLIPFORGE_DATA")
    os.environ["CLIPFORGE_DATA"] = str(tmp_path_factory.mktemp("donnees"))
    importlib.reload(config)
    yield
    if ancien is None:
        del os.environ["CLIPFORGE_DATA"]
    else:
        os.environ["CLIPFORGE_DATA"] = ancien
    importlib.reload(config)


@pytest.fixture(scope="session", autouse=True)
def ensure_fixture():
    if not FIXTURE_MP4.is_file():
        proc = subprocess.run([sys.executable, str(MAKE_FIXTURE)], check=False,
                              capture_output=True, text=True, errors="replace")
        if proc.returncode != 0:
            warnings.warn("fixture.mp4 indisponible, les tests integration vont echouer.\n"
                          f"Sortie de make_fixture.py :\n{proc.stderr[-1000:]}")


@pytest.fixture(scope="session", autouse=True)
def sans_warmup_gpu():
    """Coupe le prechauffage GPU pour toute la suite.

    Chaque TestClient declenche le lifespan, donc un chargement de Whisper
    large-v3 : des dizaines de fois sur une suite complete. La memoire finit par
    manquer et c'est un test voisin qui tombe — le rendu ffmpeg echouait sur
    « Could not open encoder », precede d'un « mkl_malloc: failed to allocate
    memory » dans le journal. Une variable d'environnement, et non un monkeypatch
    du module : les fixtures d'API rechargent clipforge.main, ce qui remettrait
    la fonction d'origine en place.
    """
    ancien = os.environ.get("CLIPFORGE_NO_WARMUP")
    os.environ["CLIPFORGE_NO_WARMUP"] = "1"
    yield
    if ancien is None:
        del os.environ["CLIPFORGE_NO_WARMUP"]
    else:
        os.environ["CLIPFORGE_NO_WARMUP"] = ancien


@pytest.fixture(scope="session", autouse=True)
def bail_gpu_desactive():
    """Coupe le bail gpu-queue pendant les tests.

    Un test ne doit jamais dependre d'un service externe ni de l'etat reel des
    cartes. Mesure le 2026-08-29 : un bail abandonne tenait la RTX 5070, et
    `test_step_transcribe_sets_words_complete_after_success` s'est mis a
    attendre qu'elle se libere — 400 s sans un mot avant d'etre tue. Le
    symptome etait un blocage, pas un echec, donc invisible dans un resume.
    """
    ancien = os.environ.get("GPUQ_URL")
    os.environ["GPUQ_URL"] = ""
    yield
    if ancien is None:
        os.environ.pop("GPUQ_URL", None)
    else:
        os.environ["GPUQ_URL"] = ancien


@pytest.fixture(scope="session", autouse=True)
def aligneur_coupe():
    """Coupe l'aligneur WhisperX pendant les tests.

    Sur ce poste il existe (C:\\tools\\whisperx) : sans cette fixture, chaque test qui transcrit
    lancerait un vrai sous-processus torch sur la carte. Les tests de l'aligneur le rebranchent
    eux-memes, sur un faux lanceur.
    """
    ancien = os.environ.get("CLIPFORGE_ALIGNEUR")
    os.environ["CLIPFORGE_ALIGNEUR"] = "0"
    yield
    if ancien is None:
        os.environ.pop("CLIPFORGE_ALIGNEUR", None)
    else:
        os.environ["CLIPFORGE_ALIGNEUR"] = ancien


@pytest.fixture(scope="session", autouse=True)
def reservation_openmp_bornee():
    """Borne les reservations du runtime OpenMP embarque dans ctranslate2.

    Sans OMP_NUM_THREADS, libiomp5md.dll provisionne pour la machine entiere des
    son initialisation : 976 Mo de commit rien que pour construire un modele
    « tiny », contre 333 Mo avec la variable. Sur la suite complete, le pic passe
    de 6354 a 3341 Mo.

    Le nombre de threads reellement utilises ne bouge pas (4 dans les deux cas,
    lu via omp_get_max_threads) : c'est la reservation qui change, pas le
    parallelisme. Le parametre cpu_threads de WhisperModel ne sert a rien ici,
    il arrive apres l'initialisation du runtime — mesure a 976 Mo lui aussi.

    4 est le defaut documente de faster-whisper : les tests exercent le meme
    parallelisme qu'en production.
    """
    ancien = os.environ.get("OMP_NUM_THREADS")
    os.environ["OMP_NUM_THREADS"] = "4"
    yield
    if ancien is None:
        del os.environ["OMP_NUM_THREADS"]
    else:
        os.environ["OMP_NUM_THREADS"] = ancien


@pytest.fixture()
def conn(tmp_path):
    c = dbmod.get_db(tmp_path / "test.db")
    yield c
    c.close()


@pytest.fixture()
def ffmpeg(monkeypatch):
    """Neutralise le journal (setup_logging remplacerait les handlers de
    caplog) et l'appel a ffmpeg.

    Partagee par test_render_queue.py, test_render_output_check.py et les
    test_montage_plan*.py (deplacee ici depuis test_render_queue.py, cf.
    rapport Task 8 -- premiere fixture partagee entre modules de test de ce
    depot). L'espion lui-meme vit dans faux_ffmpeg.py.
    """
    monkeypatch.setattr(config, "setup_logging", lambda: None)
    monkeypatch.setattr(config, "teardown_logging", lambda: None)
    monkeypatch.setattr(montage_plan_mod, "measure_loudness", lambda *a, **k: None)
    espion = FauxFfmpeg()
    espion.installer(monkeypatch)
    return espion
