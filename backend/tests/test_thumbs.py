import logging
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from clipforge.pipeline import thumbs
from clipforge.pipeline.thumbs import (
    THUMB_WIDTH,
    ThumbError,
    ensure_thumb,
    thumb_path,
    try_thumb,
)

FIXTURE = Path(__file__).parent / "fixtures" / "fixture.mp4"


def _dimensions(path: Path) -> tuple[int, int]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x", str(path)],
        capture_output=True, text=True, check=True).stdout.strip()
    width, height = out.split("x")
    return int(width), int(height)


def test_thumb_path_is_deterministic(tmp_path):
    p1 = thumb_path(tmp_path, 12, 34.5)
    p2 = thumb_path(tmp_path, 12, 34.5)
    assert p1 == p2
    assert p1.suffix == ".jpg"
    assert "vod12" in str(p1)
    assert p1.parent.name == "thumbs"


def test_thumb_path_quantizes_to_a_tenth_of_second(tmp_path):
    assert thumb_path(tmp_path, 1, 34.52) == thumb_path(tmp_path, 1, 34.54)
    assert thumb_path(tmp_path, 1, 34.5) != thumb_path(tmp_path, 1, 34.6)


@pytest.mark.integration
def test_ensure_thumb_creates_file_once(tmp_path):
    out = ensure_thumb(tmp_path, 1, FIXTURE, 5.0)
    assert out.is_file() and out.stat().st_size > 0
    assert _dimensions(out) == (THUMB_WIDTH, 270)  # fixture 1920x1080 -> 480x270
    before = out.stat().st_mtime_ns
    again = ensure_thumb(tmp_path, 1, FIXTURE, 5.0)
    assert again == out
    assert out.stat().st_mtime_ns == before  # idempotent : pas de regeneration


@pytest.mark.integration
def test_ensure_thumb_beyond_duration_raises_and_leaves_no_debris(tmp_path):
    with pytest.raises(ThumbError):
        ensure_thumb(tmp_path, 1, FIXTURE, 9999.0)
    assert not list((tmp_path / "vod1" / "thumbs").glob("*.jpg"))


def test_a_thumbnail_that_never_ends_is_killed_and_degrades(tmp_path, monkeypatch, caplog):
    """D7 : sur le TS de 19,5 Go sans index, `-ss 1717` degenere en balayage
    lineaire — trois ffmpeg tournaient encore APRES l'arret du moteur, il a fallu
    les tuer a la main. Le sous-processus doit etre borne, tue a l'expiration, et
    l'affichage se replier sur « pas de vignette ».

    Le faux processus dort trente secondes : s'il n'etait pas tue, l'appel
    rendrait la main dans trente secondes et non dans une. C'est la mort du
    processus qu'on mesure, pas seulement le message d'erreur.
    """
    monkeypatch.setattr(thumbs, "THUMB_TIMEOUT_S", 0.5)
    vrai_run = subprocess.run
    vu = {}

    def run_interminable(cmd, **kwargs):
        vu["timeout"] = kwargs.get("timeout")
        return vrai_run([sys.executable, "-c", "import time; time.sleep(30)"], **kwargs)

    monkeypatch.setattr(thumbs.subprocess, "run", run_interminable)
    debut = time.monotonic()
    with pytest.raises(ThumbError, match="abandonnee"):
        ensure_thumb(tmp_path, 1, tmp_path / "v2836283171.mp4", 1717.0)
    ecoule = time.monotonic() - debut

    assert vu["timeout"] == 0.5           # la borne est bien passee au sous-processus
    assert ecoule < 10.0, f"{ecoule:.1f}s : le processus a survecu a son delai"
    assert not list((tmp_path / "vod1" / "thumbs").glob("*"))  # ni vignette ni .tmp
    with caplog.at_level(logging.WARNING):
        assert try_thumb(tmp_path, 1, tmp_path / "v2836283171.mp4", 1717.0) is None


def test_two_concurrent_requests_spawn_a_single_ffmpeg(tmp_path, monkeypatch):
    """D7 : un ffmpeg par chargement de page, tous sur le meme instant.

    Le second appelant n'en lance pas un deuxieme : il attend le premier et lit
    le meme cache. L'API tourne dans un threadpool, d'ou les deux vrais threads.
    """
    demarre = threading.Event()
    lances = []

    def run_lent(cmd, **kwargs):
        lances.append(cmd)
        demarre.set()
        time.sleep(0.4)
        Path(cmd[-1]).write_bytes(b"jpeg")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(thumbs.subprocess, "run", run_lent)
    resultats: dict[str, object] = {}

    def appel(nom: str) -> None:
        try:
            resultats[nom] = ensure_thumb(tmp_path, 1, tmp_path / "src.mp4", 1717.0)
        except ThumbError as exc:
            resultats[nom] = exc

    premier = threading.Thread(target=appel, args=("a",))
    premier.start()
    assert demarre.wait(5), "le premier appel n'a jamais lance ffmpeg"
    second = threading.Thread(target=appel, args=("b",))
    second.start()
    premier.join(20)
    second.join(20)

    assert len(lances) == 1, f"{len(lances)} ffmpeg lances pour une seule vignette"
    attendue = thumb_path(tmp_path, 1, 1717.0)
    assert resultats == {"a": attendue, "b": attendue}


def test_a_failed_generation_frees_the_slot_for_the_next_caller(tmp_path, monkeypatch):
    """Un echec ne doit pas condamner la vignette : le suivant doit pouvoir reessayer."""
    lances = []

    def run_rate(cmd, **kwargs):
        lances.append(cmd)
        return subprocess.CompletedProcess(cmd, 1, "", "boom")

    monkeypatch.setattr(thumbs.subprocess, "run", run_rate)
    for _ in range(2):
        with pytest.raises(ThumbError):
            ensure_thumb(tmp_path, 1, tmp_path / "src.mp4", 12.0)
    assert len(lances) == 2


def test_try_thumb_logs_the_failure_and_returns_none(tmp_path, caplog):
    with caplog.at_level(logging.WARNING):
        assert try_thumb(tmp_path, 7, tmp_path / "absente.mp4", 1.0) is None
    assert "Vignette" in caplog.text


@pytest.mark.integration
def test_step_probe_generates_the_vod_thumbnail(conn, monkeypatch, tmp_path):
    from clipforge import config
    from clipforge import db as dbmod
    from clipforge.pipeline.steps import build_real_steps

    monkeypatch.setattr(config, "JOBS_DIR", tmp_path)
    vod_id = dbmod.create_vod(conn, title="t", source_path=str(FIXTURE))
    dict(build_real_steps())["probing"](conn, vod_id, {})
    vod = dbmod.get_vod(conn, vod_id)
    assert vod["duration_s"] == pytest.approx(30.0, abs=0.5)
    vignette = thumb_path(tmp_path, vod_id, vod["duration_s"] * 0.1)
    assert vignette.is_file() and vignette.stat().st_size > 0


def test_step_segment_requests_a_thumbnail_per_candidate(conn, monkeypatch, tmp_path):
    from clipforge import config
    from clipforge import db as dbmod
    from clipforge.pipeline import thumbs
    from clipforge.pipeline.steps import build_real_steps

    monkeypatch.setattr(config, "JOBS_DIR", tmp_path)
    demandes = []
    monkeypatch.setattr(thumbs, "try_thumb",
                        lambda jobs_dir, vid, src, t: demandes.append((vid, round(t, 2))))
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
