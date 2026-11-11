"""Ce qui empeche une analyse d'aboutir, constate AVANT de la lancer.

Regle du module : un controle qui echoue porte SON REMEDE. Un constat sans
action a faire est ce que la V3 affichait deja, et qui n'aidait personne.

Rien ici n'est appele par le pipeline. Le controle se demande depuis
l'interface, en tache de fond, donc sa propre panne ne peut pas interrompre une
analyse : elle ne peut que priver la banniere de son contenu.
"""
import shutil
import sqlite3
from pathlib import Path

from clipforge import config, diagnostic
from clipforge import db as dbmod

SEUIL_DISQUE_GO = 20.0
# La transcription charge le modele Whisper ET l'audio entier de la VOD. Sous ce
# plancher l'allocation ne tombe pas au demarrage, elle tombe au milieu — un des
# trois echecs de la V3 est arrive apres quarante minutes de travail.
SEUIL_MEMOIRE_GO = 4.0

INSTALLER_FFMPEG = (
    "Installe ffmpeg (winget install Gyan.FFmpeg) puis relance ClipForge : "
    "le moteur ne relit le PATH qu'a son demarrage.")
METTRE_A_JOUR_FFMPEG = (
    "Mets ffmpeg a jour (winget upgrade Gyan.FFmpeg) puis relance ClipForge : "
    "sous la 7.0, chaque rendu du montage echoue sur « Error parsing global "
    "options », un message qui ne dit pas d'ou il vient.")
SANS_GPU = (
    "Sans carte detectee la transcription bascule sur le processeur : compte des "
    "heures la ou un GPU prend des minutes. Verifie le pilote NVIDIA.")
SOURCES_PERDUES = (
    "Remets le fichier au chemin d'origine, ou supprime la VOD depuis l'accueil : "
    "elle ne pourra plus etre analysee ni exportee.")
REINDEXER = (
    "Apercu au survol et editeur inutilisables sur ces VOD. Ferme ClipForge, puis "
    "lance : cd backend && uv run python -m clipforge.migrate_seekable --apply")
DOSSIER_INJOIGNABLE = (
    "Rebranche le disque qui porte ce dossier, ou corrige CLIPFORGE_DATA, puis "
    "relance ClipForge.")
MEMOIRE_BASSE = (
    "Ferme ce qui tourne a cote avant de lancer une analyse : la transcription "
    "charge le modele et l'audio de la VOD en memoire, et l'allocation echoue au "
    "milieu du travail, pas au demarrage.")
MESURE_MEMOIRE_ABSENTE = (
    "Memoire libre non mesurable : surveille-la toi-meme dans le gestionnaire des "
    "taches avant de lancer une analyse longue.")
BASE_A_MIGRER = (
    "Ferme ClipForge et relance-le : les migrations s'appliquent a l'ouverture de "
    "la base. Si l'ecart persiste, cette base vient d'un ClipForge plus recent — "
    "ne l'ouvre pas avec celui-ci, il en ignore des colonnes.")


def _controle(cle: str, ok: bool, detail: str, action: str = "") -> dict:
    """L'action est le remede d'un echec : un controle au vert n'en porte pas,
    sinon la banniere se remplirait de conseils pour ce qui va bien."""
    return {"cle": cle, "ok": ok, "detail": detail, "action": "" if ok else action}


def _controle_outil(etat: dict, cle: str) -> dict:
    """Resolu ET capable de repondre : une entree de PATH vers un dossier
    versionne survit a la disparition du dossier, `which` la trouve encore et
    l'appel echoue quarante minutes plus tard, au milieu d'une analyse.

    Un outil par appel, parce que le pipeline les resout un par un : ffmpeg pour
    le remux et les rendus, ffprobe pour la lecture des metadonnees. Controler
    l'un pour l'autre rendrait vert sur une installation qui n'en a qu'un.
    """
    chemin = etat[f"{cle}_chemin"]
    if not chemin:
        return _controle(cle, False, etat[cle], INSTALLER_FFMPEG)
    return _controle(
        cle, not diagnostic.sonde_en_echec(etat[cle]), etat[cle],
        f"{chemin} ne repond pas. Verifie que ce chemin existe encore, "
        f"reinstalle ffmpeg au besoin, puis relance ClipForge.")


def _controle_montage(etat: dict) -> dict:
    """Avec un ffmpeg anterieur a la 7.0, TOUT rendu du montage echoue
    (-/filter_complex). Lit le verdict de machine_state, aucun appel de plus.
    Rouge seulement sur une version LUE et trop ancienne : sonde en echec et
    version non reconnue restent vertes, faute de remede propre (motifs dans
    test_health.py)."""
    verdict = etat["ffmpeg_montage_v6"]
    return _controle("ffmpeg_montage", not diagnostic.montage_hors_plancher(verdict),
                     verdict, METTRE_A_JOUR_FFMPEG)


def _espace_libre() -> float | None:
    """Go libres sur le dossier de donnees, None s'il n'est plus joignable.

    L'espace qui compte est celui du dossier de donnees : c'est la que tombent
    les telechargements, les extraits et les rendus, pas dans le repertoire
    courant du moteur. Ce dossier peut vivre sur un disque externe ou un partage
    reseau, donc disparaitre en cours de route : la panne se DECRIT, comme dans
    diagnostic.py. Levee ici, elle emporterait tous les autres controles, et la
    banniere se tairait au moment precis ou l'environnement est casse.
    """
    try:
        return shutil.disk_usage(config.DATA_DIR).free / 1024**3
    except OSError:
        return None


def _controle_disque(libre: float | None) -> dict:
    if libre is None:
        return _controle("disque", False,
                         f"dossier de donnees injoignable : {config.DATA_DIR}",
                         DOSSIER_INJOIGNABLE)
    return _controle("disque", libre >= SEUIL_DISQUE_GO, f"{libre:.1f} Go libres",
                     f"Libere de la place dans {config.DATA_DIR} : moins de "
                     f"{SEUIL_DISQUE_GO:.0f} Go disponibles, une VOD Twitch en pese "
                     f"plusieurs.")


def _controle_memoire(libre: float | str) -> dict:
    """La mesure rend un flottant OU son explication d'echec (diagnostic.py:69).

    La chaine est un echec DECRIT, pas un zero : la lire comme « 0.0 Go libres »
    ferait crier la banniere sur une machine parfaitement saine, et une banniere
    qui crie s'apprend a fermer sans lire. D'ou deux remedes distincts — liberer
    de la memoire, ou aller la surveiller soi-meme.
    """
    if isinstance(libre, str):
        return _controle("memoire", False, libre, MESURE_MEMOIRE_ABSENTE)
    return _controle("memoire", libre >= SEUIL_MEMOIRE_GO, f"{libre:.1f} Go libres",
                     MEMOIRE_BASSE)


def run_checks_without_db() -> list[dict]:
    """Controles machine : outils resolus, ressources disponibles.

    Une seule collecte pour tous. La banniere et le rapport de diagnostic
    decrivent alors la meme machine, et nvidia-smi n'est sonde qu'une fois.
    """
    etat = diagnostic.machine_state()
    return [
        _controle_outil(etat, "ffmpeg"),
        _controle_outil(etat, "ffprobe"),
        _controle_montage(etat),
        _controle_disque(_espace_libre()),
        _controle("gpu", not diagnostic.sonde_en_echec(etat["gpu"]), etat["gpu"],
                  SANS_GPU),
        _controle_memoire(etat["memoire_libre_go"]),
    ]


def _telecharge(source: Path) -> bool:
    """True si ClipForge a produit ce fichier, donc s'il a le droit de le reecrire.

    Meme regle que `step_remux` et `migrate_seekable` : un import local appartient
    au user et n'est jamais reecrit.
    """
    try:
        return source.resolve().is_relative_to(config.DOWNLOADS_DIR.resolve())
    except OSError:
        return False


def _controle_schema(conn: sqlite3.Connection) -> dict:
    """La base est-elle a la version que ce moteur sait lire ?

    La lecture est gardee pour la meme raison que _espace_libre : une base assez
    cassee pour avoir perdu sa table de version ferait lever ici, et la levee
    emporterait TOUS les controles — la banniere se tairait au moment precis ou
    la base est en cause.
    """
    attendue = dbmod.CURRENT_SCHEMA_VERSION
    try:
        row = conn.execute("SELECT version FROM schema_version").fetchone()
    except sqlite3.Error as exc:
        return _controle("schema", False, f"version illisible : {exc}", BASE_A_MIGRER)
    version = row["version"] if row else None
    if version == attendue:
        return _controle("schema", True, f"version {version}")
    return _controle("schema", False,
                     f"base en version {version if version is not None else 'inconnue'}, "
                     f"moteur en version {attendue}", BASE_A_MIGRER)


def run_db_checks(conn: sqlite3.Connection) -> list[dict]:
    """Controles sur les donnees : schema coherent, sources presentes et navigables."""
    from clipforge.pipeline import remux
    from clipforge.pipeline.runner import STEP_ORDER

    # Les statuts que le pipeline detient, ceux-la memes que main.py reconcilie
    # au demarrage quand un arret les a laisses en plan.
    en_cours = ("queued", *STEP_ORDER)
    manquantes: list[str] = []
    fragmentees: list[str] = []
    for vod in conn.execute(
            "SELECT title, source_path, status FROM vods "
            "WHERE source_path <> ''").fetchall():
        chemin = Path(vod["source_path"])
        if not chemin.is_file():
            manquantes.append(vod["title"])
            continue
        # Une source disparue reste un vrai probleme pendant une analyse (au
        # dessus) ; un conteneur non indexe, non : step_remux le reecrit lui-meme,
        # juste apres que step_download ait renseigne source_path. Le signaler
        # collerait « Ferme ClipForge » sur le travail en cours, que le user
        # interromprait pour relancer en CLI ce que le pipeline etait en train
        # de faire.
        if vod["status"] in en_cours:
            continue
        # Un fichier importe par le user ne sera reecrit par aucune commande :
        # afficher le remede de la reindexation dessus promettrait une reparation
        # qui n'aura pas lieu.
        if _telecharge(chemin) and remux.is_fragmented(chemin):
            fragmentees.append(vod["title"])
    return [
        _controle_schema(conn),
        _controle("sources", not manquantes,
                  f"{len(manquantes)} VOD sans fichier source : "
                  f"{', '.join(manquantes)}" if manquantes else "toutes presentes",
                  SOURCES_PERDUES),
        _controle("indexation", not fragmentees,
                  f"{len(fragmentees)} VOD au conteneur non indexe : "
                  f"{', '.join(fragmentees)}" if fragmentees else "toutes navigables",
                  REINDEXER),
    ]
