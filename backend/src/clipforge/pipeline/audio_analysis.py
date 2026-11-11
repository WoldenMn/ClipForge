import wave
from pathlib import Path

import numpy as np

from clipforge.pipeline.normalize import rank_normalize


def scores_from_samples(x: np.ndarray, *, sr: int, window_s: float = 10.0,
                        hop_s: float = 5.0) -> list[tuple[float, float]]:
    """RMS par fenetre glissante, normalise par RANG (0-1).

    Le rang evite la saturation observee en V1 (9 candidats a 1.00 exactement) :
    chaque fenetre se classe par rapport aux autres fenetres du meme stream.
    """
    win, hop = int(window_s * sr), int(hop_s * sr)
    if len(x) < win:
        x = np.pad(x, (0, win - len(x)))
    starts = range(0, max(1, len(x) - win + 1), hop)
    rms = [float(np.sqrt(np.mean(x[s:s + win] ** 2))) for s in starts]
    norm = rank_normalize(rms)
    return [(i * hop / sr, s) for i, s in enumerate(norm)]


def read_wav_mono(wav_path: Path | str) -> tuple[np.ndarray, int]:
    """Lit un WAV pcm_s16le mono, rend les echantillons normalises [-1,1] et le sample rate.

    Facture hors de compute_audio_scores (Task 8) : render.montage_plan lit le
    meme format pour la resolution fine (fine_energy), et les deux cotes ne
    doivent jamais diverger sur la conversion int16 -> float.
    """
    with wave.open(str(wav_path)) as w:
        if w.getsampwidth() != 2 or w.getnchannels() != 1:
            raise ValueError(
                f"WAV attendu pcm_s16le mono, recu sampwidth={w.getsampwidth()} "
                f"channels={w.getnchannels()}")
        sr = w.getframerate()
        raw = w.readframes(w.getnframes())
    x = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    return x, sr


def compute_audio_scores(wav_path: Path | str, *, window_s: float = 10.0,
                         hop_s: float = 5.0) -> list[tuple[float, float]]:
    x, sr = read_wav_mono(wav_path)
    return scores_from_samples(x, sr=sr, window_s=window_s, hop_s=hop_s)
