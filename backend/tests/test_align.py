"""Aligneur WhisperX : segments, contrat du sous-processus, replis sur faster-whisper."""
import json
import subprocess
import sys
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

from clipforge import db as dbmod
from clipforge.pipeline import align
from clipforge.pipeline.transcribe import transcribe_to_db

FAUX_SCRIPT = """
import json, sys
entree, sortie = sys.argv[2], sys.argv[3]
segs = json.load(open(entree, encoding="utf-8"))["segments"]
rendu = [[[round(m + 0.1, 3), round(m + 0.3, 3)] for m in
          [s["start"] + 0.3 + 0.5 * k for k in range(s["n"])]] for s in segs]
json.dump({"segments": rendu, "argv": sys.argv}, open(sortie, "w", encoding="utf-8"))
"""


def _wav(path: Path, seconds: float) -> Path:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x00" * int(seconds * 16000))
    return path


@pytest.fixture
def faux_aligneur(tmp_path, monkeypatch):
    """Vrai sous-processus : le Python de la suite sert de lanceur, le script est un faux."""
    script = tmp_path / "faux_aligneur.py"
    script.write_text(FAUX_SCRIPT, encoding="utf-8")
    monkeypatch.setenv("CLIPFORGE_ALIGNEUR", sys.executable)
    monkeypatch.setattr(align, "SCRIPT", script)
    return script


def test_segments_coupent_sur_pause_phrase_et_longueur(monkeypatch):
    monkeypatch.setattr(align, "SEGMENT_MAX_S", 3.0)
    mots = [(0.0, 0.4, "Bonjour"), (0.5, 0.9, "toi."), (1.3, 1.6, "Oui"),
            (3.0, 3.4, "puis"), (3.5, 3.9, "encore"), (4.0, 4.4, "et"), (4.5, 6.6, "fin")]
    segs = align.segments(mots)
    assert [(s["debut"], s["n"]) for s in segs] == [(0, 2), (2, 1), (3, 3), (6, 1)]
    assert [s["text"] for s in segs] == ["Bonjour toi.", "Oui", "puis encore et", "fin"]


def test_segments_elargis_sans_deborder_sur_le_voisin(monkeypatch):
    monkeypatch.setattr(align, "MARGE_AVANT_S", 0.3)
    monkeypatch.setattr(align, "MARGE_APRES_S", 1.2)
    mots = [(1.0, 1.4, "un"), (1.5, 2.0, "deux"), (2.9, 3.2, "trois")]
    a, b = align.segments(mots)
    assert (a["start"], a["end"]) == (0.7, 2.9)
    assert (b["start"], b["end"]) == (2.6, 4.4)
    tasse = align.segments([(0.1, 0.5, "seul")])[0]
    assert tasse["start"] == 0.0


@pytest.mark.parametrize("valeur", ["0", "", "off", "non"])
def test_lanceur_coupe(monkeypatch, valeur):
    monkeypatch.setenv("CLIPFORGE_ALIGNEUR", valeur)
    assert align.lanceur() is None


def test_lanceur_absent_et_present(monkeypatch, tmp_path):
    monkeypatch.setenv("CLIPFORGE_ALIGNEUR", str(tmp_path / "absent.cmd"))
    assert align.lanceur() is None
    monkeypatch.setenv("CLIPFORGE_ALIGNEUR", sys.executable)
    assert align.lanceur() == sys.executable


def test_carte_posee_si_le_moteur_n_en_a_pas(monkeypatch, tmp_path):
    monkeypatch.delenv("CUDA_VISIBLE_DEVICES", raising=False)
    argv, env = align._commande("py", tmp_path / "a.wav", tmp_path / "e", tmp_path / "s", 1)
    assert env["CUDA_VISIBLE_DEVICES"] == "1"
    assert argv[-2:] == ["--device-index", "0"]
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "GPU-abc,GPU-def")
    argv, env = align._commande("py", tmp_path / "a.wav", tmp_path / "e", tmp_path / "s", 1)
    assert env["CUDA_VISIBLE_DEVICES"] == "GPU-abc,GPU-def"
    assert argv[-2:] == ["--device-index", "1"]


def test_aligner_par_un_vrai_sous_processus(faux_aligneur, tmp_path):
    wav = _wav(tmp_path / "a.wav", 5.0)
    mots = [(0.5, 0.8, "un"), (1.0, 1.3, "deux"), (3.0, 3.3, "trois")]
    temps = align.aligner(wav, mots, gpu_index=0, duree_s=5.0)
    assert temps == [(0.6, 0.8), (1.1, 1.3), (3.1, 3.3)]


def test_paire_hors_fenetre_ou_inversee_garde_faster_whisper():
    seg = {"start": 1.0, "end": 2.0, "n": 3}
    assert align._temps_valides([[1.1, 1.2], [1.5, 1.4], [1.9, 2.5]], seg) == [
        (1.1, 1.2), None, None]
    assert align._temps_valides(None, seg) == [None, None, None]
    assert align._temps_valides([[1.1, 1.2]], seg) == [None, None, None]


def _faux_run(code=0, rendu=None, exc=None):
    def run(argv, **kwargs):
        if exc is not None:
            raise exc
        if rendu is not None:
            Path(argv[4]).write_text(json.dumps(rendu), encoding="utf-8")
        return SimpleNamespace(returncode=code, stderr="trace de l'aligneur")
    return run


@pytest.mark.parametrize("run", [
    _faux_run(code=1),
    _faux_run(rendu={"segments": []}),
    _faux_run(rendu={"autre": 1}),
    _faux_run(exc=subprocess.TimeoutExpired("x", 1)),
    _faux_run(exc=OSError("lanceur illisible")),
], ids=["code-1", "segments-manquants", "json-inattendu", "delai", "oserror"])
def test_aligner_en_echec_rend_none_et_le_dit(monkeypatch, tmp_path, caplog, run):
    monkeypatch.setenv("CLIPFORGE_ALIGNEUR", sys.executable)
    with caplog.at_level("WARNING"):
        assert align.aligner(tmp_path / "a.wav", [(0.5, 0.8, "un")], gpu_index=0,
                             duree_s=1.0, run=run) is None
    assert "faster-whisper gardes" in caplog.text


def test_aligner_en_base_ne_touche_que_les_temps(conn, faux_aligneur, tmp_path):
    vod_id = dbmod.create_vod(conn, title="t", source_path="x")
    conn.executemany("INSERT INTO words VALUES (?,?,?,?,?,?)",
                     [(vod_id, 0, 0.5, 0.8, "un", 0.9), (vod_id, 1, 1.0, 1.3, "deux", 0.8)])
    wav = _wav(tmp_path / "a.wav", 3.0)
    assert align.aligner_en_base(conn, vod_id, wav, gpu_index=0, duree_s=3.0) == 2
    rows = conn.execute("SELECT idx, start_s, end_s, text, probability FROM words "
                        "WHERE vod_id=? ORDER BY idx", (vod_id,)).fetchall()
    assert [tuple(r) for r in rows] == [(0, 0.6, 0.8, "un", 0.9), (1, 1.1, 1.3, "deux", 0.8)]


def _faux_whisper(monkeypatch):
    class FakeWhisperModel:
        def __init__(self, *args, **kwargs):
            pass

        def transcribe(self, audio, **kwargs):
            seg = SimpleNamespace(end=2.0, words=[
                SimpleNamespace(start=0.5, end=0.8, word=" un", probability=0.9),
                SimpleNamespace(start=1.0, end=1.3, word=" deux", probability=0.9)])
            return iter([seg]), SimpleNamespace(duration=3.0)

    monkeypatch.setattr("faster_whisper.WhisperModel", FakeWhisperModel)


def test_transcription_recale_ses_mots(conn, faux_aligneur, tmp_path, monkeypatch):
    _faux_whisper(monkeypatch)
    vod_id = dbmod.create_vod(conn, title="t", source_path="x")
    assert transcribe_to_db(conn, vod_id, _wav(tmp_path / "a.wav", 3.0), device="cpu") == 2
    rows = conn.execute("SELECT start_s FROM words WHERE vod_id=? ORDER BY idx", (vod_id,))
    assert [r[0] for r in rows] == [0.6, 1.1]


def test_panne_de_l_aligneur_ne_coute_pas_la_transcription(conn, tmp_path, monkeypatch, caplog):
    _faux_whisper(monkeypatch)

    def panne(*args, **kwargs):
        raise RuntimeError("base verrouillee")

    monkeypatch.setattr(align, "aligner_en_base", panne)
    vod_id = dbmod.create_vod(conn, title="t", source_path="x")
    with caplog.at_level("WARNING"):
        assert transcribe_to_db(conn, vod_id, _wav(tmp_path / "a.wav", 3.0), device="cpu") == 2
    rows = conn.execute("SELECT start_s FROM words WHERE vod_id=? ORDER BY idx", (vod_id,))
    assert [r[0] for r in rows] == [0.5, 1.0]
    assert "Aligneur en panne" in caplog.text
