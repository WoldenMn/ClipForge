"""Le journal moteur : ce qui l'ecrit, et ce qui le relit dans le rapport.

Dans l'application installee, Tauri lance le moteur et JETTE son flux de sortie
(src-tauri/src/engine.rs : `let (_rx, child) = ...`). Tout ce qui ne va pas sur
disque n'existe pas. C'est ce fichier que `usage.record` designe quand il
documente son exception a la regle « jamais avaler une erreur » : sans lui,
l'exception autorisee par la spec est une vraie erreur avalee.
"""
import importlib
import logging
import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor
from multiprocessing import get_context

import pytest

from clipforge import config, diagnostic

ENFANT = """
import logging
from clipforge import config
config.setup_logging()
logging.getLogger("worker").error("erreur du process enfant")
"""


@pytest.fixture()
def journal(tmp_path, monkeypatch):
    """Un journal neuf dans un dossier de donnees jetable.

    Les handlers sont retires a la sortie : un fichier laisse ouvert empeche
    Windows d'effacer le tmp_path, et le test suivant heriterait du journal du
    precedent.
    """
    monkeypatch.setenv("CLIPFORGE_DATA", str(tmp_path))
    importlib.reload(config)
    yield config
    for handler in logging.root.handlers[:]:
        logging.root.removeHandler(handler)
        handler.close()


def test_a_warning_reaches_a_real_file(journal):
    """Le symptome corrige : `logging.basicConfig(stream=sys.stdout)` seul
    envoyait tout warning et toute erreur du moteur dans le vide."""
    journal.setup_logging()
    logging.getLogger("clipforge.test").warning("le disque est plein")
    assert journal.LOG_PATH.is_file()
    assert "le disque est plein" in journal.LOG_PATH.read_text(encoding="utf-8")


def test_the_journal_never_grows_without_bound(journal, monkeypatch):
    """Un journal sans borne finit par peser plus que les VOD qu'il decrit."""
    monkeypatch.setattr(journal, "LOG_MAX_OCTETS", 4_000)
    journal.setup_logging()
    for i in range(500):
        logging.getLogger("clipforge.test").info("ligne %04d %s", i, "x" * 200)
    total = sum(f.stat().st_size for f in journal.LOG_PATH.parent.glob("moteur.log*"))
    assert total <= 4_000 * (journal.LOG_GARDES + 1) * 1.5, f"{total} octets"


def test_a_rotation_refused_by_another_process_never_loses_the_line(journal, monkeypatch):
    """Mesure sur ce poste : deux process en rotation sur le meme fichier voient
    os.rename refuse (WinError 32, fichier tenu ouvert) ou os.remove sans cible
    (WinError 2, l'autre process vient de renommer).

    RotatingFileHandler traite ces pannes par handleError : la ligne emise est
    PERDUE. Et elle l'est pendant une analyse — c'est-a-dire quand deux process
    ecrivent, c'est-a-dire quand les lignes comptent le plus.
    """
    monkeypatch.setattr(journal, "LOG_MAX_OCTETS", 200)

    def refus(self):
        self.stream.close()      # ce que fait le vrai doRollover avant de renommer
        self.stream = None
        raise PermissionError(32, "Le processus ne peut pas acceder au fichier")

    monkeypatch.setattr("logging.handlers.RotatingFileHandler.doRollover", refus)
    journal.setup_logging()
    logging.getLogger("clipforge.test").error("%s", "a" * 300)  # depasse la borne
    logging.getLogger("clipforge.test").error("la ligne qui nomme la panne")
    assert "la ligne qui nomme la panne" in journal.LOG_PATH.read_text(encoding="utf-8")


def test_a_deferred_rotation_says_so_in_the_journal(journal, monkeypatch):
    """Une rotation qui n'a pas lieu n'est pas une erreur avalee : elle se lit."""
    monkeypatch.setattr(journal, "LOG_MAX_OCTETS", 200)

    def refus(self):
        self.stream.close()
        self.stream = None
        raise PermissionError(32, "Le processus ne peut pas acceder au fichier")

    monkeypatch.setattr("logging.handlers.RotatingFileHandler.doRollover", refus)
    journal.setup_logging()
    for _ in range(5):
        logging.getLogger("clipforge.test").error("%s", "a" * 300)
    texte = journal.LOG_PATH.read_text(encoding="utf-8")
    assert "rotation reportee" in texte
    assert texte.count("rotation reportee") == 1, "un marqueur par ligne noierait le journal"


def test_the_spawned_worker_writes_into_the_same_journal(journal):
    """Le pipeline tourne dans un ProcessPoolExecutor en mp_context=spawn, et ce
    process enfant appelle setup_logging (runner.py, render/queue.py). Ses
    erreurs sont celles qui comptent le plus : si elles n'atterrissent pas ici,
    le journal decrit un moteur qui ne fait jamais rien d'interessant.

    Verifie aussi qu'un enfant qui tient le fichier ne verrouille pas le parent.
    """
    journal.setup_logging()
    logging.getLogger("clipforge.test").error("avant l'enfant")
    fils = subprocess.run([sys.executable, "-c", ENFANT], capture_output=True,
                          text=True, errors="replace", check=False)
    assert fils.returncode == 0, fils.stderr
    logging.getLogger("clipforge.test").error("apres l'enfant")
    texte = journal.LOG_PATH.read_text(encoding="utf-8")
    for attendu in ("avant l'enfant", "erreur du process enfant", "apres l'enfant"):
        assert attendu in texte, texte


def test_a_pool_worker_releases_the_journal_when_its_task_ends(journal, tmp_path):
    """Les process du pool SURVIVENT a leur tache.

    ProcessPoolExecutor n'a pas de max_tasks_per_child (main.py:129) : ses
    process vivent jusqu'a l'arret du moteur, et chaque tache rappelle
    setup_logging. Un handler laisse ouvert tient donc le fichier pour TOUTE la
    session, et Windows refuse alors le rename — passe le premier job de fond,
    la rotation ne reussirait plus jamais et le journal grossirait sans borne.
    La contention doit durer une TACHE, pas une session.

    Le parent n'ouvre volontairement pas le journal : c'est l'emprise du worker
    qu'on mesure, pas la sienne.
    """
    from clipforge.render.queue import render_clip_job
    with ProcessPoolExecutor(max_workers=1, mp_context=get_context("spawn")) as pool:
        # Un clip inexistant : le worker appelle setup_logging, puis ressort.
        pool.submit(render_clip_job, 999, str(tmp_path / "t.db"),
                    str(tmp_path), str(tmp_path)).result(timeout=120)
        assert journal.LOG_PATH.is_file(), "le worker n'a pas ouvert le journal"
        avant = journal.LOG_PATH.read_bytes()
        archive = journal.LOG_PATH.with_name("moteur.log.1")
        # Renommer est exactement ce que fait la rotation du moteur.
        journal.LOG_PATH.replace(archive)
        assert archive.read_bytes() == avant, "le rename doit deplacer le journal du worker"
        assert not journal.LOG_PATH.exists(), "le journal ne doit plus etre sous son nom"


def test_the_report_carries_the_last_lines_of_the_journal(journal, conn):
    """« Vingt dernieres lignes du journal moteur », exigees par la spec."""
    journal.setup_logging()
    for i in range(40):
        logging.getLogger("clipforge.test").error("evenement %02d", i)
    rapport = diagnostic.build_report(conn)
    assert "--- Journal moteur ---" in rapport
    assert "evenement 39" in rapport
    assert "evenement 00" not in rapport, "vingt lignes, pas le journal entier"


def test_a_missing_journal_is_explained_not_raised(journal, conn):
    """Ce module DECRIT, y compris ses propres pannes : un journal absent ne
    doit pas emporter le rapport qu'on demande justement quand ca va mal."""
    assert not journal.LOG_PATH.exists()
    rapport = diagnostic.build_report(conn)
    assert "--- Journal moteur ---" in rapport
    assert str(journal.LOG_PATH) in rapport
