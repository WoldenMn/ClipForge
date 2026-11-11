import json
import logging
import sqlite3

from pydantic import BaseModel

from clipforge.pipeline.ai_scoring import _transcript_for
from clipforge.render.sfx import VENDORED_EMOJI, emoji_path

logger = logging.getLogger(__name__)
MODEL = "claude-haiku-4-5"
MAX_HOOK_CHARS = 60
# Repli local : aligne sur la consigne du prompt IA (40 caracteres, cf. SYSTEM plus
# bas) -- un repli sature systematiquement son plafond, et 60 caracteres capitales
# passent sur 3 lignes sur la bande camera (Task 10, D5).
FALLBACK_HOOK_CHARS = 40
# Ponctuation qui ne doit jamais trainer en fin d'accroche : celle laissee par une
# coupe (virgule, point) et celle deja presente en fin de fragment de transcription
# (guillemet ouvrant orphelin notamment, vu en Task 10, D3).
_TRAILING_PUNCT = ",.;:!?«»\"' "

SYSTEM = (
    "Tu ecris les accroches d'extraits d'un stream Twitch gaming en francais. "
    "Pour chaque extrait : une accroche choc en francais (max 40 caracteres, "
    "MAJUSCULES, style TikTok, sans hashtag), un titre de fichier sobre "
    f"(max 60 caracteres), et au plus UN emoji pertinent parmi : {VENDORED_EMOJI}, "
    "sinon null. "
    "Retourne une entree par extrait recu, en recopiant son clip_id exactement."
)


class ClipWriting(BaseModel):
    clip_id: int
    hook: str
    titre: str
    emoji: str | None = None


class WritingResult(BaseModel):
    clips: list[ClipWriting]


def _truncate_word_safe(text: str, limit: int) -> str:
    """Tronque `text` a `limit` caracteres sans jamais couper un mot en deux, puis
    retire la ponctuation trainante (coupe sur le dernier espace <= limit ; un
    seul mot plus long que `limit` est coupe dur, faute d'alternative).

    Les blancs sont d'abord normalises : des blancs doubles mangeaient le budget
    de caracteres, laissaient des mots vides en fin d'accroche et se voyaient sur
    la bande (test reel du 2026-08-30). Ils venaient alors des mots de la
    transcription, qui portaient leur espace de tete ; transcribe.py les strippe
    depuis, et l'assemblage passe par clipforge.text.join_words. La
    normalisation reste la defense contre une chaine ecrite par l'IA.

    Corollaire de cette jonction : une elision recollee ne porte plus d'espace
    interne, donc la coupe sur le dernier espace ne peut plus la trancher."""
    text = " ".join(text.split())
    if len(text) > limit:
        head = text[:limit]
        cut = head.rfind(" ")
        text = head[:cut] if cut > 0 else head
    return text.rstrip(_TRAILING_PUNCT)


def _normalize_hook(raw: str | None) -> str | None:
    """Vide -> None ; sinon plafond DEFENSIF de MAX_HOOK_CHARS sur ce que l'IA
    renvoie (elle est censee tenir les 40 caracteres du prompt : ce plafond n'est
    qu'un garde-fou geometrique).

    La coupe passe par _truncate_word_safe, comme celle du repli local : une
    accroche depassant le plafond s'arretait au 60e caractere quel qu'il soit, et
    ce garde-fou geometrique livrait un mot tranche en plein milieu de la bande
    camera. Le plafond borne la LARGEUR, il n'a jamais eu vocation a decider ou
    un mot se termine.
    """
    text = (raw or "").strip()
    if not text:
        return None
    return _truncate_word_safe(text, MAX_HOOK_CHARS) or None


def _fallback_hook(raw: str | None) -> str | None:
    """Accroche de repli local (pas d'IA) : jamais coupee en plein mot, plafond
    aligne sur la consigne IA (FALLBACK_HOOK_CHARS)."""
    text = (raw or "").strip()
    if not text:
        return None
    return _truncate_word_safe(text, FALLBACK_HOOK_CHARS) or None


def _fallback_title(row: sqlite3.Row, hook: str | None) -> str:
    """Jamais vide : titre existant, sinon l'accroche, sinon un nom generique."""
    existing = (row["title"] or "").strip()
    return existing or hook or f"Clip {row['id']}"


def _validate_emoji(raw: str | None, hook: str | None) -> str | None:
    """Un emoji sans accroche ne peut jamais s'ecrire (l'incrustation est calee sur
    la fenetre du hook) ; un emoji hors du jeu vendorise devient NULL."""
    if not hook:
        return None
    char = (raw or "").strip()
    return char if char and emoji_path(char) else None


def _apply_fallback(conn: sqlite3.Connection, row: sqlite3.Row) -> None:
    hook = _fallback_hook(row["hook_text"])
    conn.execute("UPDATE clips SET hook=?, title=?, emoji=NULL WHERE id=?",
                 (hook, _fallback_title(row, hook), row["id"]))


def write_clips(conn: sqlite3.Connection, vod_id: int, clip_ids: list[int]) -> bool:
    """Ecrit hook/titre/emoji via l'API Claude. False = repli local applique (total ou partiel)."""
    import anthropic

    if not clip_ids:
        return True
    placeholders = ",".join("?" * len(clip_ids))
    rows = conn.execute(
        "SELECT clips.id AS id, clips.title AS title, clips.start_s AS start_s, "
        "clips.end_s AS end_s, segments.hook_text AS hook_text FROM clips "
        "LEFT JOIN segments ON segments.id = clips.segment_id "
        f"WHERE clips.vod_id=? AND clips.id IN ({placeholders})",
        (vod_id, *clip_ids)).fetchall()
    if not rows:
        return True
    by_id = {r["id"]: r for r in rows}
    payload = [{"clip_id": r["id"],
                "transcription": _transcript_for(conn, vod_id, r["start_s"], r["end_s"])}
               for r in rows]
    try:
        client = anthropic.Anthropic(max_retries=4, timeout=120.0)
        response = client.messages.parse(
            model=MODEL, max_tokens=4096, system=SYSTEM,
            messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
            output_format=WritingResult)
        if response.stop_reason == "max_tokens":
            raise RuntimeError("Sortie IA tronquee")
        result: WritingResult | None = response.parsed_output
        if result is None:
            raise RuntimeError(
                f"Reponse IA sans sortie structuree (stop_reason={response.stop_reason})")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Ecriture IA echouee, repli local applique : %s", exc)
        for row in rows:
            _apply_fallback(conn, row)
        conn.commit()
        return False

    written_ids: set[int] = set()
    for item in result.clips:
        row = by_id.get(item.clip_id)
        if row is None:
            continue
        hook = _normalize_hook(item.hook)
        title = (item.titre or "").strip() or _fallback_title(row, hook)
        conn.execute("UPDATE clips SET hook=?, title=?, emoji=? WHERE id=?",
                     (hook, title, _validate_emoji(item.emoji, hook), row["id"]))
        written_ids.add(item.clip_id)

    all_covered = True
    for row in rows:
        if row["id"] not in written_ids:
            _apply_fallback(conn, row)
            all_covered = False
    conn.commit()
    return all_covered
