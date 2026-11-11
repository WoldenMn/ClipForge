# pyright: reportMissingImports=false
"""Aligne les segments de faster-whisper sur l'audio par le wav2vec2 francais de WhisperX.

Tourne dans le venv de WhisperX, jamais dans celui de ClipForge (torch n'y est pas) :
  <python de WhisperX> aligner_mots.py <audio.wav> <segments.json> <sortie.json> [--device-index N]

Entree : {"segments": [{"start", "end", "text"}]}, le texte etant les mots de ClipForge joints
par une espace. Sortie : {"segments": [[[debut, fin] | null, ...] | null, ...]}, une paire par mot
d'entree, ou null pour un segment que l'aligneur n'a pas su rendre mot pour mot : ClipForge garde
alors les temps de faster-whisper. Chaque segment se lit seul dans le WAV (pcm_s16le mono 16 kHz) :
la memoire ne depend pas de la duree de la VOD.
"""

import argparse
import json
import sys
import wave
from pathlib import Path

import numpy as np
from whisperx import alignment

MODELE_FR = "jonatasgrosman/wav2vec2-large-xlsr-53-french"
SR = 16000


def lire_fenetre(wav: wave.Wave_read, debut_s: float, fin_s: float) -> np.ndarray:
    wav.setpos(min(wav.getnframes(), int(debut_s * SR)))
    brut = wav.readframes(max(0, int((fin_s - debut_s) * SR)))
    return np.frombuffer(brut, dtype=np.int16).astype(np.float32) / 32768.0


def aligner_segment(segment: dict, audio: np.ndarray, modele, meta, device: str) -> list | None:
    attendus = len(segment["text"].split(" "))
    texte = [{"start": 0.0, "end": len(audio) / SR, "text": segment["text"]}]
    res = alignment.align(texte, modele, meta, audio, device)
    mots = [m for s in res["segments"] for m in s.get("words", [])]
    if len(mots) != attendus:
        return None
    t0 = segment["start"]
    return [[round(t0 + m["start"], 3), round(t0 + m["end"], 3)]
            if "start" in m and "end" in m else None for m in mots]


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("audio")
    p.add_argument("entree")
    p.add_argument("sortie")
    p.add_argument("--device-index", type=int, default=0)
    a = p.parse_args()
    segments = json.loads(Path(a.entree).read_text(encoding="utf-8"))["segments"]
    device = f"cuda:{a.device_index}"
    modele, meta = alignment.load_align_model("fr", device, model_name=MODELE_FR)
    sortie = []
    with wave.open(a.audio) as wav:
        if wav.getframerate() != SR or wav.getnchannels() != 1 or wav.getsampwidth() != 2:
            print("WAV attendu pcm_s16le mono 16 kHz", file=sys.stderr)
            return 2
        for segment in segments:
            audio = lire_fenetre(wav, segment["start"], segment["end"])
            ok = len(audio) > 0
            sortie.append(aligner_segment(segment, audio, modele, meta, device) if ok else None)
    Path(a.sortie).write_text(json.dumps({"segments": sortie}), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
