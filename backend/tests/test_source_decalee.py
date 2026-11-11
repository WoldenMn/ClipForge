"""Une vraie source dont le conteneur ne demarre pas a 0 (revue finale V6, fix
round 3, F2) : ffmpeg et ffprobe reels, rien de simule.

La source du relecteur (decale_video_courte.mp4) demarrait a 1,479 s, video
de 1,5 a 28,0 s. Celle-ci en reprend la forme en plus court : conteneur a
1,477 s, video de 1,5 a 6,0 s, audio de 1,477 a 7,5 s. -ss compte depuis le
debut du fichier, donc la video finit a 4,523 s et l'audio a 6,023 s.
"""
import json
import subprocess
import wave
from pathlib import Path

import pytest
from test_render_queue import _clip, _rendre, _seed

from clipforge import db as dbmod
from clipforge.pipeline.audio_extract import extract_wav
from clipforge.pipeline.probe import probe_media

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def source_decalee(tmp_path_factory) -> Path:
    src = tmp_path_factory.mktemp("decalee") / "decalee.mp4"
    proc = subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate=30:duration=4.5",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=6", "-c:v", "libx264",
         "-preset", "veryfast", "-pix_fmt", "yuv420p", "-c:a", "aac",
         "-output_ts_offset", "1.5", str(src)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    assert proc.returncode == 0, proc.stderr[-800:]
    brut = json.loads(subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams",
         str(src)], capture_output=True, text=True, check=True).stdout)
    assert float(brut["format"]["start_time"]) == pytest.approx(1.477, abs=0.01), brut
    video = next(s for s in brut["streams"] if s["codec_type"] == "video")
    assert float(video["start_time"]) == pytest.approx(1.5, abs=0.01), brut
    assert float(video["duration"]) == pytest.approx(4.5, abs=0.01), brut
    return src


def test_un_rendu_qui_touche_la_fin_de_la_video_decalee_est_livre(tmp_path, source_decalee):
    """V5 [2 ; 6] : 2,52 s de video existent apres 2 s. Lue sans le debut du
    conteneur, la video finissait a 6,0 s, rien ne manquait, et ce rendu sain
    etait refuse."""
    db_path = tmp_path / "clipforge.db"
    clip_id = _seed(db_path, source=source_decalee)
    conn = dbmod.get_db(db_path)
    try:
        conn.execute("UPDATE clips SET start_s=2.0, end_s=6.0 WHERE id=?", (clip_id,))
        conn.commit()
    finally:
        conn.close()
    _rendre(tmp_path, clip_id)
    clip = _clip(tmp_path, clip_id)
    assert clip["status"] == "done", clip["error"]
    rendu = probe_media(clip["output_path"])
    assert rendu.video_duration_s == pytest.approx(2.52, abs=0.1), rendu


def test_l_extraction_qui_touche_la_fin_de_l_audio_decale_est_acceptee(
        tmp_path, source_decalee):
    """[4,5 ; 7,5] : 1,52 s d'audio apres 4,5 s. Lue sans le debut du
    conteneur, 3,0 s etaient disponibles et l'extraction etait refusee."""
    wav = extract_wav(source_decalee, tmp_path / "a.wav", start_s=4.5, end_s=7.5)
    assert wav == tmp_path / "a.wav"
    with wave.open(str(wav)) as w:
        canaux, cadence, trames = w.getnchannels(), w.getframerate(), w.getnframes()
    assert (canaux, cadence) == (1, 16000)
    assert trames / cadence == pytest.approx(1.52, abs=0.05), trames
