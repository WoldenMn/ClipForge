import time
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

from clipforge import db as dbmod
from clipforge.pipeline.audio_extract import extract_wav
from clipforge.pipeline.transcribe import TranscriptionError, iter_chunks, transcribe_to_db

FIXTURE = Path(__file__).parent / "fixtures" / "fixture.mp4"


def _write_silence_wav(path: Path, seconds: float, sr: int = 16000) -> Path:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(b"\x00\x00" * int(seconds * sr))
    return path


def test_iter_chunks_boundaries():
    assert iter_chunks(0.0) == []
    assert iter_chunks(30.0, chunk_s=1200.0, overlap_s=5.0) == [(0.0, 30.0, 0.0)]
    assert iter_chunks(50.0, chunk_s=20.0, overlap_s=5.0) == [
        (0.0, 20.0, 0.0), (15.0, 40.0, 20.0), (35.0, 50.0, 40.0)]
    assert iter_chunks(40.0, chunk_s=20.0, overlap_s=5.0) == [
        (0.0, 20.0, 0.0), (15.0, 40.0, 20.0)]


@pytest.mark.integration
def test_transcribe_mechanics(conn, tmp_path):
    vod_id = dbmod.create_vod(conn, title="t", source_path=str(FIXTURE))
    wav = extract_wav(FIXTURE, tmp_path / "a.wav")
    seen = []
    transcribe_to_db(conn, vod_id, wav, model_name="tiny", device="cpu",
                     compute_type="int8", progress_cb=seen.append)
    rows = conn.execute("SELECT * FROM words WHERE vod_id=?", (vod_id,)).fetchall()
    # La fixture est une mire sonore : 0 mot est acceptable, la mecanique ne doit pas crasher
    assert isinstance(rows, list)
    assert seen == [] or seen[-1] <= 100.0


def test_transcribe_chunked_offsets_and_dedupe(conn, tmp_path, monkeypatch):
    monkeypatch.setattr("clipforge.pipeline.transcribe.CHUNK_S", 20.0)
    monkeypatch.setattr("clipforge.pipeline.transcribe.OVERLAP_S", 5.0)
    vod_id = dbmod.create_vod(conn, title="t", source_path="x")
    wav = _write_silence_wav(tmp_path / "50s.wav", 50.0)

    def words(*starts):
        return [SimpleNamespace(start=s, end=s + 0.4, word=f" w{s}", probability=0.9)
                for s in starts]

    # indexe par LONGUEUR de tranche : les workers paralleles arrivent dans un
    # ordre non deterministe, un compteur d'appels rendrait le test flaky
    by_len = {
        20 * 16000: [SimpleNamespace(end=19.0, words=words(14.0, 16.0, 18.0))],
        25 * 16000: [SimpleNamespace(end=10.0, words=words(0.0, 6.0))],
        15 * 16000: [SimpleNamespace(end=8.0, words=words(1.0, 7.0))],
    }
    calls = []

    class FakeWhisperModel:
        def __init__(self, *args, **kwargs):
            pass

        def transcribe(self, audio, **kwargs):
            calls.append(len(audio))
            return iter(by_len[len(audio)]), SimpleNamespace(duration=None)

    monkeypatch.setattr("faster_whisper.WhisperModel", FakeWhisperModel)
    n = transcribe_to_db(conn, vod_id, wav, model_name="tiny", device="cpu",
                         compute_type="int8")
    assert sorted(calls) == [15 * 16000, 20 * 16000, 25 * 16000]
    rows = conn.execute(
        "SELECT idx, start_s FROM words WHERE vod_id=? ORDER BY idx", (vod_id,)).fetchall()
    # chunk 2 demarre a 15 s : le mot relatif 0.0 (abs 15.0 < keep 20.0) est un doublon filtre
    # chunk 3 demarre a 35 s : le mot relatif 1.0 (abs 36.0 < keep 40.0) est filtre
    assert n == 5
    assert [r["start_s"] for r in rows] == [14.0, 16.0, 18.0, 21.0, 42.0]
    assert [r["idx"] for r in rows] == [0, 1, 2, 3, 4]


def test_transcribe_gpu_failure_at_inference_falls_back_cpu(conn, tmp_path, monkeypatch):
    vod_id = dbmod.create_vod(conn, title="t", source_path="x")
    wav = _write_silence_wav(tmp_path / "5s.wav", 5.0)
    instances = []

    class FakeWhisperModel:
        def __init__(self, *args, **kwargs):
            self.device = kwargs.get("device", args[1] if len(args) > 1 else "cuda")
            instances.append(self.device)

        def transcribe(self, audio, **kwargs):
            if self.device == "cuda":
                raise RuntimeError("Library cublas64_12.dll is not found")
            seg = SimpleNamespace(end=2.0, words=[
                SimpleNamespace(start=1.0, end=1.4, word=" ok", probability=0.9)])
            return iter([seg]), SimpleNamespace(duration=5.0)

    monkeypatch.setattr("faster_whisper.WhisperModel", FakeWhisperModel)
    n = transcribe_to_db(conn, vod_id, wav, model_name="tiny", device="cuda",
                         compute_type="float16")
    assert n == 1
    assert instances == ["cuda", "cpu"]
    row = conn.execute("SELECT start_s, text FROM words WHERE vod_id=?", (vod_id,)).fetchone()
    assert row["start_s"] == 1.0 and row["text"] == "ok"


def test_transcribe_purges_partial_words_on_failure(conn, tmp_path, monkeypatch):
    vod_id = dbmod.create_vod(conn, title="t", source_path="x")
    wav = _write_silence_wav(tmp_path / "court.wav", 2.0)

    def make_words(n, offset):
        return [SimpleNamespace(start=offset + i * 0.001, end=offset + i * 0.001 + 0.0005,
                                word=f" mot{i}", probability=0.9) for i in range(n)]

    def fake_segments():
        yield SimpleNamespace(end=1.0, words=make_words(300, 0.0))
        yield SimpleNamespace(end=2.0, words=make_words(300, 1.0))
        raise RuntimeError("boom")

    class FakeWhisperModel:
        def __init__(self, *args, **kwargs):
            pass

        def transcribe(self, *args, **kwargs):
            return fake_segments(), SimpleNamespace(duration=2.0)

    monkeypatch.setattr("faster_whisper.WhisperModel", FakeWhisperModel)

    with pytest.raises(TranscriptionError):
        transcribe_to_db(conn, vod_id, wav, model_name="tiny", device="cpu",
                         compute_type="int8")

    count = conn.execute("SELECT COUNT(*) FROM words WHERE vod_id=?", (vod_id,)).fetchone()[0]
    assert count == 0


def test_transcribe_parallel_preserves_order(conn, tmp_path, monkeypatch):
    monkeypatch.setattr("clipforge.pipeline.transcribe.CHUNK_S", 20.0)
    monkeypatch.setattr("clipforge.pipeline.transcribe.OVERLAP_S", 5.0)
    vod_id = dbmod.create_vod(conn, title="t", source_path="x")
    wav = _write_silence_wav(tmp_path / "60s.wav", 60.0)
    import threading
    concurrent = {"max": 0, "now": 0}
    lock = threading.Lock()

    class FakeWhisperModel:
        def __init__(self, *args, **kwargs):
            pass

        def transcribe(self, audio, **kwargs):
            with lock:
                concurrent["now"] += 1
                concurrent["max"] = max(concurrent["max"], concurrent["now"])
            time.sleep(0.15)
            with lock:
                concurrent["now"] -= 1
            n = len(audio) // 16000
            seg = SimpleNamespace(end=float(n), words=[
                SimpleNamespace(start=float(n) - 1.0, end=float(n) - 0.5,
                                word=" x", probability=0.9)])
            return iter([seg]), SimpleNamespace(duration=None)

    monkeypatch.setattr("faster_whisper.WhisperModel", FakeWhisperModel)
    transcribe_to_db(conn, vod_id, wav, model_name="tiny", device="cpu",
                     compute_type="int8")
    rows = conn.execute(
        "SELECT idx, start_s FROM words WHERE vod_id=? ORDER BY idx", (vod_id,)).fetchall()
    starts = [r["start_s"] for r in rows]
    assert starts == sorted(starts)  # ordre temporel preserve malgre le parallelisme
    assert [r["idx"] for r in rows] == list(range(len(rows)))
    assert concurrent["max"] >= 2  # au moins 2 tranches traitees en parallele
