import logging
import logging.handlers
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]

try:
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env")
except ImportError:
    pass
DATA_DIR = Path(os.environ.get("CLIPFORGE_DATA") or PROJECT_ROOT / "data")
DB_PATH = DATA_DIR / "clipforge.db"
DOWNLOADS_DIR = DATA_DIR / "downloads"
JOBS_DIR = DATA_DIR / "jobs"
OUTPUT_DIR = DATA_DIR / "output"
LOG_PATH = DATA_DIR / "moteur.log"
FRONTEND_DIST = PROJECT_ROOT / "frontend" / "dist"
TAURI_CONF = PROJECT_ROOT / "src-tauri" / "tauri.conf.json"

# Trois generations de 2 Mo : de quoi couvrir plusieurs analyses sans jamais
# peser a cote des VOD que le journal decrit.
LOG_MAX_OCTETS = 2 * 1024**2
LOG_GARDES = 2

TWITCHDOWNLOADER_CLI = (
    os.environ.get("TWITCHDOWNLOADER_CLI")
    or r"C:\tools\TwitchDownloaderCLI\TwitchDownloaderCLI.exe"
)
WHISPER_MODEL = os.environ.get("CLIPFORGE_WHISPER_MODEL") or "large-v3"


def _gpu_index(raw: str | None) -> int:
    """Index CUDA de la carte Whisper (CLIPFORGE_GPU_INDEX, defaut 0).

    faster-whisper/ctranslate2 ne choisissent la carte qu'a la construction du
    modele (device_index) : aucune variable posee apres l'init ne rattrape un
    mauvais choix. Une valeur illisible doit donc arreter le moteur tout de
    suite, pas charger le modele sur la carte d'affichage en silence.
    """
    if raw is None or raw.strip() == "":
        return 0
    try:
        return int(raw)
    except ValueError as exc:
        raise RuntimeError(f"CLIPFORGE_GPU_INDEX illisible : {raw!r} (entier attendu)") from exc


WHISPER_GPU_INDEX = _gpu_index(os.environ.get("CLIPFORGE_GPU_INDEX"))


def ensure_dirs() -> None:
    for d in (DATA_DIR, DOWNLOADS_DIR, JOBS_DIR, OUTPUT_DIR):
        d.mkdir(parents=True, exist_ok=True)


class JournalPartage(logging.handlers.RotatingFileHandler):
    """Rotation qui cede le pas au journal, jamais l'inverse.

    Trois process ecrivent ici : le moteur, le worker d'analyse et celui de
    rendu — les deux derniers spawnes, et les deux appelant setup_logging
    (pipeline/runner.py, render/queue.py). Mesure sur ce poste, deux process en
    rotation sur le meme fichier : `os.rename` est refuse (WinError 32, fichier
    tenu ouvert par l'autre) ou `os.remove` ne trouve plus sa cible (WinError 2,
    l'autre vient de renommer). Aucune corruption de contenu et aucun verrou :
    seule la rotation se dispute.

    RotatingFileHandler traite ces pannes par handleError, qui imprime une trace
    et PERD la ligne emise. Or elle se perd pendant une analyse — c'est-a-dire
    quand deux process ecrivent, c'est-a-dire quand les lignes comptent le plus.
    On garde donc la ligne et on laisse la rotation au tour suivant : le fichier
    depasse sa borne le temps de la contention, jamais au-dela — a condition que
    les workers RENDENT le fichier en sortant, ce dont teardown_logging se
    charge. Sans lui la contention durerait toute la session, pas une tache.
    """

    _rotation_reportee = False

    def doRollover(self) -> None:
        try:
            super().doRollover()
            self._rotation_reportee = False
        except OSError as exc:
            # doRollover a ferme le flux avant d'echouer : sans cette
            # reouverture, la ligne en cours et toutes les suivantes tomberaient.
            if self.stream is None:
                self.stream = self._open()
            if not self._rotation_reportee:
                # Un marqueur par episode, pas par ligne : repete a chaque emit,
                # il noierait ce qu'on vient au journal pour lire.
                self._rotation_reportee = True
                self.stream.write(f"[journal] rotation reportee, "
                                  f"un autre process tient le fichier : {exc}\n")


def setup_logging() -> None:
    """Sortie standard ET fichier, parce que la sortie standard ne suffit pas.

    Dans l'application installee, Tauri lance le moteur et jette son flux de
    sortie (src-tauri/src/engine.rs : `let (_rx, child) = ...`). Tout ce qui ne
    va pas sur disque n'existe pas — y compris l'echec d'usage.record, qui
    documente son exception a la regle « jamais avaler une erreur » en renvoyant
    ICI. Le rapport de diagnostic relit ce fichier.

    force=True parce que la fonction est rejouee : les fixtures d'API rechargent
    clipforge.main avec un autre CLIPFORGE_DATA, et sans remplacement des
    handlers le journal continuerait d'ecrire dans le dossier precedent.
    """
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    echec = ""
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(JournalPartage(LOG_PATH, maxBytes=LOG_MAX_OCTETS,
                                       backupCount=LOG_GARDES, encoding="utf-8"))
    except OSError as exc:
        # CLIPFORGE_DATA sur un disque debranche : le moteur demarre quand meme,
        # mais il le DIT plutot que de tourner sans journal a l'insu de tous.
        echec = f"Journal moteur indisponible ({LOG_PATH}) : {exc}"
    logging.basicConfig(level=logging.INFO, handlers=handlers, force=True,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    if echec:
        logging.getLogger(__name__).error(echec)


def teardown_logging() -> None:
    """Rend le journal en fin de tache de worker. A appeler dans un `finally`.

    Les process du pool SURVIVENT a leur tache : ProcessPoolExecutor n'a pas de
    max_tasks_per_child (main.py), ses process vivent jusqu'a l'arret du moteur.
    Un handler laisse ouvert tient donc le fichier pour toute la session, et
    Windows refuse le rename a qui veut roter — passe le premier job de fond, la
    rotation echouerait a jamais et la borne de taille ne voudrait plus rien
    dire. La tache suivante rouvre ce qu'il lui faut en appelant setup_logging.
    """
    for handler in logging.root.handlers[:]:
        logging.root.removeHandler(handler)
        handler.close()
