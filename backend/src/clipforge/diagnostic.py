"""Etat de la machine et rapport transmissible.

Aucune valeur d'environnement n'est recopiee : la presence d'une cle API est
notee, jamais sa valeur. Toute sonde d'outil externe qui echoue rend une chaine
explicative — ce module ne leve pas, il DECRIT, y compris les pannes. C'est
justement quand la machine va mal qu'on lui demande ce rapport.
"""
import ctypes
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

DELAI_SONDE_S = 10
USAGE_AFFICHE = 40
DETAIL_MAX = 1500
JOURNAL_AFFICHE = 20
# Le montage V6 passe son graphe a ffmpeg par -/filter_complex (render.ffmpeg_cmd),
# option apparue avec ffmpeg 7.0 (Changelog : « -/opt <path> »).
FFMPEG_MONTAGE_MIN = (7, 0)
_FFMPEG_VERSION_RE = re.compile(r"ffmpeg version n?(\d+)\.(\d+)")
# Debut du verdict qui dit une version LUE et sous le plancher. Meme regle que
# DEBUTS_ECHEC : le vocabulaire vit ici, health.py le teste par la fonction
# ci-dessous plutot que par une chaine recopiee qu'un reformulage perimerait.
MONTAGE_TROP_ANCIEN = "trop ancien"

# Vocabulaire d'echec des sondes de ce module : elles rendent une explication a
# la place d'un resultat. Le controle de sante (health.py) s'en sert pour ne pas
# prendre un message d'erreur pour un outil en etat de marche. Cette liste vit
# ici et nulle part ailleurs : recopiee la-bas, un simple reformulage ici y
# rouvrirait le faux vert sans que rien ne le signale.
DEBUTS_ECHEC = ("introuvable", "nvidia-smi introuvable", "aucune sortie",
                "version illisible", "appel impossible", "appel en echec")


def sonde_en_echec(valeur: str) -> bool:
    """True si la sonde a rendu son explication d'echec au lieu d'un resultat."""
    return valeur.startswith(DEBUTS_ECHEC)


def _appel(argv: list[str]) -> str:
    """Sortie standard d'une sonde, ou l'explication de son echec.

    Le code de retour n'est pas decoratif : nvidia-smi refuse un argument sur sa
    SORTIE STANDARD, avec le code 2 et rien sur stderr. Sans ce controle,
    « ERROR: Option ... is not recognized » passerait pour la description du GPU.
    """
    try:
        sortie = subprocess.run(argv, capture_output=True, text=True, errors="replace",
                                timeout=DELAI_SONDE_S, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return f"appel impossible : {exc}"
    if sortie.returncode != 0:
        motif = (sortie.stderr or sortie.stdout).strip().splitlines()
        return (f"appel en echec (code {sortie.returncode}) : "
                f"{motif[0] if motif else 'aucun message'}")
    return sortie.stdout.strip()


def _version_outil(exe: str) -> str:
    """Premiere ligne de `-version`, commune a ffmpeg et a ffprobe."""
    lignes = _appel([exe, "-version"]).splitlines()
    return lignes[0] if lignes else "version illisible"


def ffmpeg_pour_le_montage(version: str) -> str:
    """Verdict sur la premiere ligne de `ffmpeg -version` pour le montage V6 :
    "ok", ou pourquoi ses rendus risquent d'echouer. Une version trop ancienne ou
    que ce module ne sait pas lire (build de developpement) est dite en clair ;
    une sonde en echec est rendue telle quelle."""
    if sonde_en_echec(version):
        return version
    minimum = f"{FFMPEG_MONTAGE_MIN[0]}.{FFMPEG_MONTAGE_MIN[1]}"
    lue = _FFMPEG_VERSION_RE.match(version)
    if lue is None:
        return (f"version non reconnue : le montage V6 demande ffmpeg {minimum} ou plus "
                f"(-/filter_complex)")
    if (int(lue.group(1)), int(lue.group(2))) < FFMPEG_MONTAGE_MIN:
        return (f"{MONTAGE_TROP_ANCIEN} ({lue.group(1)}.{lue.group(2)}) : le montage V6 "
                f"demande ffmpeg {minimum} ou plus (-/filter_complex), ses rendus "
                f"echoueraient")
    return "ok"


def montage_hors_plancher(verdict: str) -> bool:
    """True quand ffmpeg_pour_le_montage a LU une version et l'a trouvee sous le
    plancher. Faux pour "ok", pour une version non reconnue et pour une sonde en
    echec : ces deux-la ne prouvent rien contre ffmpeg."""
    return verdict.startswith(MONTAGE_TROP_ANCIEN)


def _gpu() -> str:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return "nvidia-smi introuvable"
    lignes = _appel(
        [exe, "--query-gpu=name,memory.free", "--format=csv,noheader"]).splitlines()
    # Une ligne par carte, reassemblees : le rapport tient une cle par ligne.
    return " | ".join(ligne.strip() for ligne in lignes) or "aucune sortie"


def _memoire_libre_go() -> float | str:
    """Memoire disponible, sans dependance supplementaire.

    Rend une chaine quand la mesure echoue plutot qu'un zero : dans un rapport de
    diagnostic, « 0.0 Go libres » se lirait comme une machine a l'agonie.
    """
    if sys.platform != "win32":
        return "mesure disponible sur Windows uniquement"

    class Statut(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

    statut = Statut()
    statut.dwLength = ctypes.sizeof(Statut)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(statut)):
        return "mesure refusee par Windows"
    return round(statut.ullAvailPhys / 1024**3, 1)


def anthropic_key_presence() -> str:
    """Presence de la cle IA, jamais sa valeur — lecture pure, sans sonde.

    Isole de machine_state() pour que le statut de la cle reste lisible SEUL,
    sans payer les sondes ffmpeg/ffprobe/GPU (subprocess, jusqu'a
    DELAI_SONDE_S=10 s chacune au pire) : /api/settings/env n'a besoin que de
    cette seule variable d'environnement.
    """
    return "presente" if os.environ.get("ANTHROPIC_API_KEY") else "absente"


def machine_state() -> dict:
    """Etat de la machine au moment ou on le demande.

    ffmpeg et ffprobe sont releves separement parce que le pipeline les resout
    separement : remux, extraction audio, apercus et rendus appellent « ffmpeg »,
    pipeline/probe.py appelle « ffprobe ». Une installation partielle (un
    ffmpeg.exe tiers deja dans le PATH, une copie manuelle d'un seul binaire) a
    l'un sans l'autre.
    """
    exe = shutil.which("ffmpeg")
    sonde = shutil.which("ffprobe")
    version = _version_outil(exe) if exe else "introuvable"
    return {
        "ffmpeg": version,
        "ffmpeg_montage_v6": ffmpeg_pour_le_montage(version),
        "ffmpeg_chemin": exe or "",
        "ffprobe": _version_outil(sonde) if sonde else "introuvable",
        "ffprobe_chemin": sonde or "",
        "gpu": _gpu(),
        "disque_libre_go": round(shutil.disk_usage(os.getcwd()).free / 1024**3, 1),
        "memoire_libre_go": _memoire_libre_go(),
        "python": sys.version.split()[0],
        "anthropic_key": anthropic_key_presence(),
    }


def version_clipforge() -> str:
    """Version de l'application, lue a sa source unique.

    tauri.conf.json numerote l'installateur : c'est ce numero que l'utilisateur
    voit et peut citer. Le recopier ici en ferait une seconde verite, qui
    divergerait au premier bump oublie. Illisible, on le DIT — un rapport qui
    tait sur quel code il tourne perd la moitie de sa raison d'etre.
    """
    from clipforge import config
    try:
        return json.loads(config.TAURI_CONF.read_text(encoding="utf-8"))["version"]
    except (OSError, ValueError, KeyError) as exc:
        return f"indeterminee ({config.TAURI_CONF} : {exc})"


def _taille_lisible(octets: int) -> str:
    """Go au-dela du gigaoctet, Mo en dessous : « 0.00 Go » sur un fichier de
    200 Mo se lit comme un fichier vide, et detourne du vrai probleme."""
    if octets >= 1024**3:
        return f"{octets / 1024**3:.2f} Go"
    return f"{octets / 1024**2:.1f} Mo"


def _fiche_source(chemin_texte: str) -> str:
    """Taille du fichier source et etat de son conteneur, ou pourquoi ils manquent.

    Ce sont ces deux mesures qui separent « ffmpeg absent » de « conteneur non
    indexe » de « disque plein ». Une source disparue est justement ce qu'on
    vient chercher dans un rapport : elle se DECRIT, comme le reste du module.
    """
    if not chemin_texte:
        return "    source : aucune (VOD Twitch pas encore telechargee)"
    chemin = Path(chemin_texte)
    try:
        taille = chemin.stat().st_size
    except OSError as exc:
        return f"    source : {chemin} illisible ({exc})"
    from clipforge.pipeline import remux
    # Meme vocabulaire que le controle de sante : « navigable » dit ce qui
    # compte, la capacite a se seeker par requetes Range.
    conteneur = "conteneur non indexe" if remux.is_fragmented(chemin) else "navigable"
    return f"    source : {chemin} — {_taille_lisible(taille)}, {conteneur}"


def _lisible(valeur: object, absent: str = "?") -> str:
    """Une mesure jamais prise est INCONNUE : un rapport constelle de « None »
    se lit comme un logiciel casse, et detourne le regard de la vraie panne."""
    return absent if valeur is None else str(valeur)


def _lignes_vods(conn: sqlite3.Connection) -> list[str]:
    lignes = []
    for vod in conn.execute("SELECT * FROM vods ORDER BY id").fetchall():
        mots = conn.execute("SELECT COUNT(*) c FROM words WHERE vod_id=?",
                            (vod["id"],)).fetchone()["c"]
        candidats = conn.execute("SELECT COUNT(*) c FROM segments WHERE vod_id=?",
                                 (vod["id"],)).fetchone()["c"]
        lignes.append(
            f"[{vod['id']}] {vod['title']} — {vod['status']}, "
            f"{vod['duration_s'] or 0:.0f} s, "
            f"{_lisible(vod['width'])}x{_lisible(vod['height'])}, "
            f"{mots} mots, {candidats} candidats")
        lignes.append(_fiche_source(vod["source_path"]))
        if vod["error"]:
            lignes.append(f"    erreur : {vod['error']}")
        if vod["error_detail"]:
            # La FIN de la trace, comme runner.py qui garde deja [-2000:] : c'est
            # la que se trouve l'exception. Couper la tete d'une fin retirerait
            # du rapport la seule ligne qui nomme l'erreur.
            lignes.append(f"    detail : {vod['error_detail'][-DETAIL_MAX:]}")
    return lignes or ["aucune VOD importee"]


def _lignes_journal() -> list[str]:
    """Les dernieres lignes du journal moteur, ou la raison de leur absence.

    Ce module DECRIT : un journal illisible ne doit pas emporter le rapport
    qu'on demande justement quand la machine va mal. Le chemin est nomme dans
    les deux cas d'absence — sans lui, « journal introuvable » n'apprend rien a
    qui doit aller voir.
    """
    from clipforge import config
    try:
        texte = config.LOG_PATH.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return [(f"aucun journal a {config.LOG_PATH} — le moteur n'a pas encore "
                 f"tourne, ou son dossier de donnees a change")]
    except OSError as exc:
        return [f"journal illisible ({config.LOG_PATH}) : {exc}"]
    lignes = texte.splitlines()
    return lignes[-JOURNAL_AFFICHE:] or [f"journal vide ({config.LOG_PATH})"]


def build_report(conn: sqlite3.Connection) -> str:
    """Rapport transmissible, en texte brut : lisible tel quel dans un message."""
    version = conn.execute("SELECT version FROM schema_version").fetchone()
    lignes = [
        "=== ClipForge — rapport de diagnostic ===",
        f"Date : {datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')}",
        f"ClipForge : {version_clipforge()}",
        f"Schema de base : version {version['version'] if version else 'inconnue'}",
        "",
        "--- Machine ---",
    ]
    lignes += [f"{cle} : {valeur}" for cle, valeur in machine_state().items()]
    lignes += ["", "--- VODs ---", *_lignes_vods(conn), "", "--- Usage recent ---"]
    evenements = [
        f"{ev['created_at']} {ev['kind']} vod={_lisible(ev['vod_id'], '-')} "
        f"rang={_lisible(ev['rank'], '-')} signal={_lisible(ev['top_signal'], '-')} "
        f"debut={_lisible(ev['start_shift_s'], '-')} fin={_lisible(ev['end_shift_s'], '-')}"
        for ev in conn.execute("SELECT * FROM usage_events ORDER BY id DESC LIMIT ?",
                               (USAGE_AFFICHE,)).fetchall()
    ]
    lignes += evenements or ["aucun evenement enregistre"]
    lignes += ["", "--- Journal moteur ---", *_lignes_journal()]
    return "\n".join(lignes)
