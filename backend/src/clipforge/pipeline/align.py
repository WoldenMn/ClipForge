"""Recale les mots de faster-whisper sur l'audio, par l'aligneur wav2vec2 francais de WhisperX.

faster-whisper place le debut de ses mots 100 a 120 ms trop tot, avec une dispersion p90 de 130 a
140 ms ; recales, ils tombent a +55 a +80 ms, dispersion 33 a 47 ms (banc du 2026-10-30,
docs/adr/001-horodatage-des-mots.md). Le texte ne change pas : seuls start_s et end_s bougent.

L'aligneur tourne dans le venv de WhisperX, que ClipForge n'embarque pas (torch, 2 Go) : un
sous-processus lance `aligneur/aligner_mots.py` par CLIPFORGE_ALIGNEUR. Il est une amelioration,
jamais une condition : absent, en echec ou hors delai, les temps de faster-whisper restent, et le
journal le dit. Un segment qu'il ne rend pas mot pour mot garde ses temps d'origine.
"""
import json
import logging
import os
import sqlite3
import subprocess
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAUT_ALIGNEUR = r"C:\tools\whisperx\python-local.cmd"
SCRIPT = Path(__file__).resolve().parents[3] / "aligneur" / "aligner_mots.py"
_DESACTIVE = {"", "0", "non", "off"}

PAUSE_S = 0.8
PHRASE_PAUSE_S = 0.3
SEGMENT_MAX_S = 25.0
MARGE_AVANT_S = 0.3
MARGE_APRES_S = 0.3
DELAI_FIXE_S = 180.0
DELAI_PAR_SECONDE_S = 0.2


def lanceur() -> str | None:
    """Commande qui lance le Python de WhisperX, ou None si l'alignement est coupe ou absent."""
    brut = os.environ.get("CLIPFORGE_ALIGNEUR", DEFAUT_ALIGNEUR).strip()
    if brut.lower() in _DESACTIVE:
        return None
    if not Path(brut).is_file():
        logger.info("Aligneur absent (%s) : temps de mots de faster-whisper", brut)
        return None
    return brut


def segments(mots: list[tuple[float, float, str]]) -> list[dict]:
    """Groupe les mots en segments a aligner : pause > PAUSE_S, phrase finie suivie d'une pause
    > PHRASE_PAUSE_S, ou SEGMENT_MAX_S atteint. Chaque fenetre s'elargit de MARGE_AVANT_S et
    MARGE_APRES_S sans deborder sur ses voisines ; `debut` est l'indice de son premier mot."""
    groupes: list[list[int]] = []
    for i, (debut, fin, _texte) in enumerate(mots):
        if groupes:
            dernier = groupes[-1]
            precedent = mots[dernier[-1]]
            pause = debut - precedent[1]
            phrase = precedent[2].rstrip().endswith((".", "?", "!")) and pause > PHRASE_PAUSE_S
            if pause > PAUSE_S or phrase or fin - mots[dernier[0]][0] > SEGMENT_MAX_S:
                groupes.append([i])
                continue
            dernier.append(i)
        else:
            groupes.append([i])
    sortie = []
    for k, g in enumerate(groupes):
        debut = mots[g[0]][0] - MARGE_AVANT_S
        fin = mots[g[-1]][1] + MARGE_APRES_S
        if k > 0:
            debut = max(debut, mots[groupes[k - 1][-1]][1])
        if k + 1 < len(groupes):
            fin = min(fin, mots[groupes[k + 1][0]][0])
        sortie.append({"start": round(max(0.0, debut), 3), "end": round(fin, 3),
                       "text": " ".join(mots[i][2] for i in g), "debut": g[0], "n": len(g)})
    return sortie


def _commande(cmd: str, wav: Path, entree: Path, sortie: Path,
              gpu_index: int) -> tuple[list[str], dict]:
    """La carte suit CLIPFORGE_GPU_INDEX, comme faster-whisper : posee par CUDA_VISIBLE_DEVICES si
    le moteur n'en a pas, sinon passee en indice parmi les cartes deja visibles."""
    env = dict(os.environ)
    if "CUDA_VISIBLE_DEVICES" in env:
        indice = gpu_index
    else:
        env["CUDA_VISIBLE_DEVICES"] = str(gpu_index)
        indice = 0
    argv = [cmd, str(SCRIPT), str(wav), str(entree), str(sortie), "--device-index", str(indice)]
    return argv, env


def _temps_valides(paires, segment: dict) -> list[tuple[float, float] | None]:
    """Une paire par mot, ou None quand elle sort de la fenetre du segment ou s'inverse."""
    if not isinstance(paires, list) or len(paires) != segment["n"]:
        return [None] * segment["n"]
    out: list[tuple[float, float] | None] = []
    for p in paires:
        if (isinstance(p, list) and len(p) == 2
                and segment["start"] - 0.05 <= p[0] <= p[1] <= segment["end"] + 0.05):
            out.append((float(p[0]), float(p[1])))
        else:
            out.append(None)
    return out


def aligner(wav_path: Path | str, mots: list[tuple[float, float, str]], *, gpu_index: int,
            duree_s: float, run=subprocess.run) -> list[tuple[float, float] | None] | None:
    """Temps recales, un par mot (None : garder celui de faster-whisper), ou None si l'aligneur
    n'a rien rendu. `run` est injectable pour les tests (signature de subprocess.run)."""
    cmd = lanceur()
    if cmd is None or not mots:
        return None
    segs = segments(mots)
    with tempfile.TemporaryDirectory(prefix="clipforge-align-") as dossier:
        entree, sortie = Path(dossier) / "segments.json", Path(dossier) / "temps.json"
        entree.write_text(json.dumps({"segments": segs}, ensure_ascii=False), encoding="utf-8")
        argv, env = _commande(cmd, Path(wav_path), entree, sortie, gpu_index)
        delai = DELAI_FIXE_S + DELAI_PAR_SECONDE_S * duree_s
        try:
            proc = run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       stdin=subprocess.DEVNULL, timeout=delai, check=False, env=env)
        except (OSError, subprocess.TimeoutExpired) as exc:
            logger.warning("Aligneur en echec (%s) : temps de faster-whisper gardes", exc)
            return None
        if proc.returncode != 0 or not sortie.is_file():
            logger.warning("Aligneur en echec (code %s) : temps de faster-whisper gardes. %s",
                           proc.returncode, (proc.stderr or "")[-400:])
            return None
        try:
            rendu = json.loads(sortie.read_text(encoding="utf-8"))["segments"]
        except (ValueError, KeyError, TypeError) as exc:
            logger.warning("Aligneur illisible (%s) : temps de faster-whisper gardes", exc)
            return None
    if not isinstance(rendu, list) or len(rendu) != len(segs):
        logger.warning("Aligneur : %s segments rendus pour %s envoyes, temps de faster-whisper "
                       "gardes", len(rendu) if isinstance(rendu, list) else "?", len(segs))
        return None
    temps: list[tuple[float, float] | None] = []
    for seg, paires in zip(segs, rendu, strict=True):
        temps.extend(_temps_valides(paires, seg))
    return temps


def aligner_en_base(conn: sqlite3.Connection, vod_id: int, wav_path: Path | str, *,
                    gpu_index: int, duree_s: float, run=subprocess.run) -> int:
    """Recale les mots de `vod_id` en base ; rend le nombre de mots recales."""
    lignes = conn.execute("SELECT idx, start_s, end_s, text FROM words WHERE vod_id=? ORDER BY idx",
                          (vod_id,)).fetchall()
    mots = [(r[1], r[2], r[3]) for r in lignes]
    temps = aligner(wav_path, mots, gpu_index=gpu_index, duree_s=duree_s, run=run)
    if temps is None:
        return 0
    maj = [(t[0], t[1], vod_id, r[0]) for r, t in zip(lignes, temps, strict=True) if t is not None]
    conn.executemany("UPDATE words SET start_s=?, end_s=? WHERE vod_id=? AND idx=?", maj)
    conn.commit()
    logger.info("Aligneur : %s mots recales sur %s", len(maj), len(lignes))
    return len(maj)
