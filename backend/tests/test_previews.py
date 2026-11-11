import logging
import subprocess
from pathlib import Path

import pytest

from clipforge.pipeline.previews import (
    PREVIEW_DURATION_S,
    PREVIEW_WIDTH,
    PreviewError,
    ensure_preview,
    preview_path,
    try_preview,
)

FIXTURE = Path(__file__).parent / "fixtures" / "fixture.mp4"


def _probe(path: Path, entries: str) -> str:
    return subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", entries,
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True).stdout.strip()


def test_preview_path_is_deterministic(tmp_path):
    p1 = preview_path(tmp_path, 12, 34.5)
    p2 = preview_path(tmp_path, 12, 34.5)
    assert p1 == p2
    assert p1.suffix == ".mp4"
    assert "vod12" in str(p1)
    assert p1.parent.name == "previews"


def test_preview_path_quantizes_to_a_tenth_of_second(tmp_path):
    assert preview_path(tmp_path, 1, 34.52) == preview_path(tmp_path, 1, 34.54)
    assert preview_path(tmp_path, 1, 34.5) != preview_path(tmp_path, 1, 34.6)


def test_preview_path_does_not_collide_with_the_thumbnail(tmp_path):
    from clipforge.pipeline.thumbs import thumb_path
    assert preview_path(tmp_path, 1, 5.0) != thumb_path(tmp_path, 1, 5.0)


@pytest.mark.integration
def test_ensure_preview_creates_file_once(tmp_path):
    out = ensure_preview(tmp_path, 1, FIXTURE, 5.0)
    assert out.is_file() and out.stat().st_size > 0
    assert _probe(out, "stream=width") == str(PREVIEW_WIDTH)
    before = out.stat().st_mtime_ns
    again = ensure_preview(tmp_path, 1, FIXTURE, 5.0)
    assert again == out
    assert out.stat().st_mtime_ns == before  # idempotent : pas de regeneration


@pytest.mark.integration
def test_ensure_preview_lasts_the_expected_duration(tmp_path):
    out = ensure_preview(tmp_path, 1, FIXTURE, 5.0)
    duree = float(_probe(out, "stream=duration"))
    assert duree == pytest.approx(PREVIEW_DURATION_S, abs=0.5)


@pytest.mark.integration
def test_ensure_preview_drops_the_audio_track(tmp_path):
    """La carte lit en muet : transporter l'audio ne ferait que peser."""
    out = ensure_preview(tmp_path, 1, FIXTURE, 5.0)
    pistes = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index",
         "-of", "csv=p=0", str(out)],
        capture_output=True, text=True, check=True).stdout.strip()
    assert pistes == ""


@pytest.mark.integration
def test_ensure_preview_stays_far_below_a_copy(tmp_path):
    """Le reencodage 480p tient en quelques dizaines de Ko la ou une copie pese des Mo."""
    out = ensure_preview(tmp_path, 1, FIXTURE, 5.0)
    assert out.stat().st_size < 500_000


@pytest.mark.integration
def test_ensure_preview_beyond_duration_raises_and_leaves_no_debris(tmp_path):
    with pytest.raises(PreviewError):
        ensure_preview(tmp_path, 1, FIXTURE, 9999.0)
    assert not list((tmp_path / "vod1" / "previews").glob("*.mp4"))


def test_try_preview_logs_the_failure_and_returns_none(tmp_path, caplog):
    with caplog.at_level(logging.WARNING):
        assert try_preview(tmp_path, 7, tmp_path / "absente.mp4", 1.0) is None
    assert "Apercu" in caplog.text


def test_step_segment_requests_a_preview_per_candidate(conn, monkeypatch, tmp_path):
    from clipforge import config
    from clipforge import db as dbmod
    from clipforge.pipeline import previews, thumbs
    from clipforge.pipeline.steps import build_real_steps

    monkeypatch.setattr(config, "JOBS_DIR", tmp_path)
    demandes = []
    monkeypatch.setattr(previews, "try_preview",
                        lambda jobs_dir, vid, src, t: demandes.append((vid, round(t, 2))))
    monkeypatch.setattr(thumbs, "try_thumb", lambda *a: None)
    vod_id = dbmod.create_vod(conn, title="t", source_path="x.mp4")
    conn.execute("UPDATE vods SET duration_s=600 WHERE id=?", (vod_id,))
    for t in range(0, 600, 5):
        score = 0.95 if 95 <= t <= 105 else 0.1
        conn.execute("INSERT INTO signals VALUES (?,?,?,?)", (vod_id, "audio", float(t), score))
    conn.commit()
    dict(build_real_steps())["segmenting"](conn, vod_id, {})
    starts = [r["start_s"] for r in conn.execute(
        "SELECT start_s FROM segments WHERE vod_id=? ORDER BY rank", (vod_id,))]
    assert starts
    assert demandes == [(vod_id, round(s + thumbs.CANDIDATE_LEAD_S, 2)) for s in starts]
