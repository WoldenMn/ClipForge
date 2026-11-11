"""Genere sfx/{whoosh,impact}.wav : bruitages procéduraux, commités une fois.

A lancer via `uv run python scripts/make_sfx.py` depuis backend/. Les WAV produits
sont commités dans le depot ; l'app ne regenere jamais rien au runtime (contrainte
hors-ligne).
"""
import wave
from pathlib import Path

import numpy as np

SR = 44100
OUT = Path(__file__).resolve().parents[1] / "src" / "clipforge" / "render" / "assets" / "sfx"

# Seed fixe : le script est relance rarement, mais toujours avec le meme resultat
# quand il l'est (revue de diff propre sur les WAV commités).
RNG_SEED = 20260830


def _write_wav(path: Path, samples: np.ndarray) -> None:
    peak = np.abs(samples).max()
    if peak > 0:
        samples = samples / peak * 0.9
    pcm = (samples * 32767.0).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())


def make_whoosh(duration_s: float = 0.45, f_start: float = 200.0, f_end: float = 2000.0
               ) -> np.ndarray:
    """Bruit blanc filtre passe-bande, balayage f_start -> f_end, enveloppe en cloche.

    Filtre resonant (state-variable, Chamberlin) pilote par une frequence de
    coupure qui glisse lineairement sur toute la duree : le contenu spectral du
    bruit se deplace dans le temps, contrairement a un filtre statique.
    """
    n = int(SR * duration_s)
    rng = np.random.default_rng(RNG_SEED)
    noise = rng.uniform(-1.0, 1.0, n)
    sweep = np.linspace(f_start, f_end, n)
    q = 0.6
    low = 0.0
    band = 0.0
    out = np.empty(n)
    for i in range(n):
        f = 2.0 * np.sin(np.pi * sweep[i] / SR)
        high = noise[i] - low - q * band
        band += f * high
        low += f * band
        out[i] = band
    return out * np.hanning(n)


def make_impact(duration_s: float = 0.35, freq: float = 65.0, decay: float = 14.0
               ) -> np.ndarray:
    """Sinus grave amorti (le coup) + souffle bref de bruit (la transitoire d'attaque)."""
    n = int(SR * duration_s)
    t = np.linspace(0.0, duration_s, n, endpoint=False)
    sine = np.sin(2.0 * np.pi * freq * t) * np.exp(-decay * t)
    rng = np.random.default_rng(RNG_SEED + 1)
    burst_n = int(SR * 0.03)
    burst = np.zeros(n)
    burst[:burst_n] = rng.uniform(-1.0, 1.0, burst_n) * np.exp(-200.0 * t[:burst_n])
    return sine * 0.85 + burst * 0.5


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    _write_wav(OUT / "whoosh.wav", make_whoosh())
    _write_wav(OUT / "impact.wav", make_impact())
    for name in ("whoosh.wav", "impact.wav"):
        print(f"{name}: {(OUT / name).stat().st_size} octets")


if __name__ == "__main__":
    main()
