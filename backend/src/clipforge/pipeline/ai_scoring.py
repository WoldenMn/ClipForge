import json
import logging
import sqlite3

from pydantic import BaseModel

from clipforge.text import join_words

logger = logging.getLogger(__name__)
MODEL = "claude-haiku-4-5"
MAX_SEGMENTS = 30
MAX_WORDS_PER_SEGMENT = 200

SYSTEM = (
    "Tu analyses des extraits de transcription d'un stream Twitch gaming en francais. "
    "Pour chaque segment : score de potentiel viral 0-100 (drole, clutch, punchline, "
    "moment fort), un titre court accrocheur en francais (max 60 caracteres), "
    "et 3-5 hashtags francais pertinents. "
    "Retourne une entree par segment recu, en recopiant son segment_id exactement."
)


class SegmentScore(BaseModel):
    segment_id: int
    score: float
    titre: str
    hashtags: list[str]


class ScoringResult(BaseModel):
    segments: list[SegmentScore]


def _transcript_for(conn: sqlite3.Connection, vod_id: int, start_s: float, end_s: float) -> str:
    rows = conn.execute(
        "SELECT text FROM words WHERE vod_id=? AND start_s>=? AND end_s<=? "
        "ORDER BY idx LIMIT ?", (vod_id, start_s, end_s, MAX_WORDS_PER_SEGMENT)).fetchall()
    return join_words(r["text"] for r in rows)


def score_segments_ai(conn: sqlite3.Connection, vod_id: int) -> bool:
    """Affine le classement via l'API Claude. False = echec (classement local conserve)."""
    import anthropic

    segs = conn.execute(
        "SELECT id, start_s, end_s FROM segments WHERE vod_id=? ORDER BY score DESC LIMIT ?",
        (vod_id, MAX_SEGMENTS)).fetchall()
    if not segs:
        return True
    payload = [{"segment_id": s["id"],
                "transcription": _transcript_for(conn, vod_id, s["start_s"], s["end_s"])}
               for s in segs]
    payload = [p for p in payload if p["transcription"]]
    if not payload:
        return True
    try:
        client = anthropic.Anthropic(max_retries=4, timeout=120.0)
        response = client.messages.parse(
            model=MODEL, max_tokens=8192, system=SYSTEM,
            messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
            output_format=ScoringResult)
        if response.stop_reason == "max_tokens":
            raise RuntimeError("Sortie IA tronquee")
        result: ScoringResult | None = response.parsed_output
        if result is None:
            raise RuntimeError(
                f"Reponse IA sans sortie structuree (stop_reason={response.stop_reason})")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Scoring IA echoue, classement local conserve : %s", exc)
        return False
    for seg in result.segments:
        score = min(max(seg.score, 0.0), 100.0)
        conn.execute(
            "UPDATE segments SET ai_score=?, ai_title=?, ai_hashtags=? WHERE id=? AND vod_id=?",
            (score, seg.titre, json.dumps(seg.hashtags, ensure_ascii=False),
             seg.segment_id, vod_id))
    conn.commit()
    return True
