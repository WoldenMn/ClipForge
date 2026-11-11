import logging
import sqlite3
import wave
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from clipforge import config
from clipforge.gpu import register_cuda_dlls

logger = logging.getLogger(__name__)

CHUNK_S = 1200.0
OVERLAP_S = 5.0
TRANSCRIBE_WORKERS = 2

_GPU_FAILURE_MARKERS = ("cublas", "cudnn", "cuda", "dll", "nvrtc")


class TranscriptionError(RuntimeError):
    pass


def iter_chunks(total_s: float, *, chunk_s: float = CHUNK_S,
                overlap_s: float = OVERLAP_S) -> list[tuple[float, float, float]]:
    """Tranches (start_s, end_s, keep_from_s) couvrant [0, total_s].

    L'extraction de features de faster-whisper materialise le spectrogramme du
    fichier ENTIER (2,5 Go pour 2 h 20, >10 Go pour 8 h) : on transcrit par
    tranches. Chaque tranche apres la premiere demarre overlap_s plus tot ;
    seuls les mots commencant a keep_from_s (temps absolu) sont conserves,
    pour ne pas couper un mot a la frontiere sans creer de doublon.
    """
    if total_s <= 0:
        return []
    chunks = []
    boundary = 0.0
    while boundary < total_s:
        start = max(0.0, boundary - (overlap_s if boundary > 0 else 0.0))
        end = min(total_s, boundary + chunk_s)
        chunks.append((start, end, boundary))
        boundary += chunk_s
    return chunks


def _looks_like_gpu_failure(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(marker in text for marker in _GPU_FAILURE_MARKERS)


def _transcribe_chunk(model, wav_path, chunk, sr):
    """Transcrit une tranche et retourne ses mots en temps ABSOLU (thread worker)."""
    import numpy as np

    start_s, end_s, keep_from_s = chunk
    with wave.open(str(wav_path)) as w:
        w.setpos(int(start_s * sr))
        raw = w.readframes(int((end_s - start_s) * sr))
    audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    segments, _info = model.transcribe(audio, language="fr", beam_size=5,
                                       word_timestamps=True, vad_filter=True)
    out = []
    for seg in segments:
        for word in seg.words or []:
            abs_start = start_s + word.start
            if abs_start < keep_from_s:
                continue
            out.append((abs_start, start_s + word.end, word.word.strip(), word.probability))
    return out


def _transcribe_all(model, conn: sqlite3.Connection, vod_id: int, wav_path: Path | str,
                    progress_cb: Callable[[float], None] | None) -> int:
    """Transcrit par tranches, TRANSCRIBE_WORKERS en parallele.

    ctranslate2 libere le GIL pendant l'inference et le GPU etait a 24 % en V1 :
    deux tranches simultanees reduisent le temps total sans changer le modele.
    Les resultats sont reordonnes par temps absolu avant ecriture.
    """
    conn.execute("DELETE FROM words WHERE vod_id=?", (vod_id,))
    conn.commit()
    with wave.open(str(wav_path)) as w:
        if w.getsampwidth() != 2 or w.getnchannels() != 1:
            raise TranscriptionError(
                f"WAV attendu pcm_s16le mono, recu sampwidth={w.getsampwidth()} "
                f"channels={w.getnchannels()}")
        sr = w.getframerate()
        total_s = w.getnframes() / sr
    chunks = iter_chunks(total_s, chunk_s=CHUNK_S, overlap_s=OVERLAP_S)
    results: list[list[tuple[float, float, str, float]]] = [[] for _ in chunks]
    done = 0
    with ThreadPoolExecutor(max_workers=TRANSCRIBE_WORKERS) as pool:
        futures = {pool.submit(_transcribe_chunk, model, wav_path, c, sr): i
                   for i, c in enumerate(chunks)}
        for fut in as_completed(futures):
            results[futures[fut]] = fut.result()
            done += 1
            if progress_cb and chunks:
                progress_cb(min(100.0, done / len(chunks) * 100.0))
    idx, batch = 0, []
    for chunk_words in results:
        for abs_start, abs_end, text, prob in chunk_words:
            batch.append((vod_id, idx, abs_start, abs_end, text, prob))
            idx += 1
            if len(batch) >= 500:
                conn.executemany("INSERT INTO words VALUES (?,?,?,?,?,?)", batch)
                conn.commit()
                batch = []
    if batch:
        conn.executemany("INSERT INTO words VALUES (?,?,?,?,?,?)", batch)
        conn.commit()
    return idx


def transcribe_to_db(conn: sqlite3.Connection, vod_id: int, wav_path: Path | str, *,
                     model_name: str = "large-v3", device: str = "cuda",
                     compute_type: str = "float16",
                     device_index: int | None = None,
                     progress_cb: Callable[[float], None] | None = None) -> int:
    register_cuda_dlls()
    from faster_whisper import WhisperModel  # import APRES enregistrement des DLL

    # La carte se choisit ICI et nulle part ailleurs : ctranslate2 lit
    # device_index a la construction, CUDA_VISIBLE_DEVICES pose apres l'init
    # du process ne change plus rien.
    gpu_index = config.WHISPER_GPU_INDEX if device_index is None else device_index
    try:
        model = WhisperModel(model_name, device=device, device_index=gpu_index,
                             compute_type=compute_type)
    except Exception as exc:
        if device != "cuda":
            raise
        logger.warning("CUDA indisponible (%s) : repli CPU int8, transcription tres ralentie", exc)
        device = "cpu"
        model = WhisperModel(model_name, device="cpu", compute_type="int8")
    try:
        n = _transcribe_all(model, conn, vod_id, wav_path, progress_cb)
    except TranscriptionError:
        conn.execute("DELETE FROM words WHERE vod_id=?", (vod_id,))
        conn.commit()
        raise
    except Exception as exc:
        conn.execute("DELETE FROM words WHERE vod_id=?", (vod_id,))
        conn.commit()
        if not (device == "cuda" and _looks_like_gpu_failure(exc)):
            raise TranscriptionError(f"Transcription echouee : {exc}") from exc
        # ctranslate2 charge cublas/cudnn paresseusement : l'echec GPU peut
        # surgir a la premiere inference, apres un constructeur reussi
        logger.warning("Echec GPU a l'inference (%s) : repli CPU int8", exc)
        del model
        model = WhisperModel(model_name, device="cpu", compute_type="int8")
        try:
            n = _transcribe_all(model, conn, vod_id, wav_path, progress_cb)
        except Exception as exc2:
            conn.execute("DELETE FROM words WHERE vod_id=?", (vod_id,))
            conn.commit()
            raise TranscriptionError(f"Transcription echouee : {exc2}") from exc2
    del model
    _recaler(conn, vod_id, wav_path, gpu_index)
    return n


def _recaler(conn: sqlite3.Connection, vod_id: int, wav_path: Path | str, gpu_index: int) -> None:
    """Passe les mots a l'aligneur une fois le modele libere : les deux ne tiennent pas la VRAM
    ensemble. Une panne de l'aligneur ne coute jamais la transcription, elle se journalise."""
    from clipforge.pipeline.align import aligner_en_base

    with wave.open(str(wav_path)) as w:
        duree_s = w.getnframes() / w.getframerate()
    try:
        aligner_en_base(conn, vod_id, wav_path, gpu_index=gpu_index, duree_s=duree_s)
    except Exception:
        logger.exception("Aligneur en panne : temps de faster-whisper gardes")
