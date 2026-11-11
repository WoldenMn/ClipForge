import json
from unittest.mock import MagicMock, patch

from clipforge import db as dbmod
from clipforge.pipeline.ai_scoring import ScoringResult, SegmentScore, score_segments_ai


def _seed(conn):
    vod_id = dbmod.create_vod(conn, title="t", source_path="x.mp4")
    conn.execute("INSERT INTO segments (vod_id, start_s, end_s, score) VALUES (?,?,?,?)",
                 (vod_id, 10.0, 40.0, 0.9))
    conn.execute("INSERT INTO segments (vod_id, start_s, end_s, score) VALUES (?,?,?,?)",
                 (vod_id, 100.0, 130.0, 0.7))
    for i, t in enumerate([12.0, 15.0, 105.0]):
        conn.execute("INSERT INTO words VALUES (?,?,?,?,?,?)",
                     (vod_id, i, t, t + 0.4, f"mot{i}", 0.9))
    conn.commit()
    return vod_id


def test_ai_scoring_merges_results(conn):
    vod_id = _seed(conn)
    seg_ids = [r["id"] for r in conn.execute(
        "SELECT id FROM segments WHERE vod_id=? ORDER BY id", (vod_id,))]
    fake = MagicMock()
    fake.parsed_output = ScoringResult(segments=[
        SegmentScore(segment_id=seg_ids[0], score=85.0, titre="Le clutch", hashtags=["#fr"]),
        SegmentScore(segment_id=seg_ids[1], score=20.0, titre="Bof", hashtags=[]),
    ])
    fake.stop_reason = "end_turn"
    with patch("anthropic.Anthropic") as cls:
        cls.return_value.messages.parse.return_value = fake
        ok = score_segments_ai(conn, vod_id)
    assert ok is True
    row = conn.execute("SELECT * FROM segments WHERE id=?", (seg_ids[0],)).fetchone()
    assert row["ai_score"] == 85.0 and row["ai_title"] == "Le clutch"
    assert json.loads(row["ai_hashtags"]) == ["#fr"]


def test_ai_scoring_fallback_on_error(conn):
    vod_id = _seed(conn)
    with patch("anthropic.Anthropic") as cls:
        cls.return_value.messages.parse.side_effect = RuntimeError("api down")
        ok = score_segments_ai(conn, vod_id)
    assert ok is False  # echec signale, classement local conserve
    assert conn.execute("SELECT COUNT(*) FROM segments WHERE ai_score IS NOT NULL"
                        ).fetchone()[0] == 0


def test_ai_scoring_max_tokens_no_write(conn):
    vod_id = _seed(conn)
    fake = MagicMock()
    fake.parsed_output = None
    fake.stop_reason = "max_tokens"
    with patch("anthropic.Anthropic") as cls:
        cls.return_value.messages.parse.return_value = fake
        ok = score_segments_ai(conn, vod_id)
    assert ok is False
    assert conn.execute("SELECT COUNT(*) FROM segments WHERE ai_score IS NOT NULL"
                        ).fetchone()[0] == 0


def test_la_transcription_envoyee_a_l_ia_recolle_les_elisions(conn):
    """L'IA recopie ce qu'elle lit : un transcript en « c 'est » ressort en
    titre et en accroche, puis dans le nom du fichier livre."""
    from clipforge.pipeline.ai_scoring import _transcript_for

    vod_id = dbmod.create_vod(conn, title="t", source_path="x.mp4")
    for i, mot in enumerate(["Non", "c", "'est", "pas", "moi", "allez", "-vous"]):
        conn.execute("INSERT INTO words VALUES (?,?,?,?,?,?)",
                     (vod_id, i, 10.0 + i, 10.4 + i, mot, 0.9))
    conn.commit()
    assert _transcript_for(conn, vod_id, 0.0, 100.0) == "Non c'est pas moi allez-vous"
