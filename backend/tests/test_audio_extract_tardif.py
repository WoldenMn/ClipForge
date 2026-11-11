"""Regression du decalage de tete, ffmpeg reel, sur deux sources fabriquees.

Constat d'origine : l'extraction de la VOD entiere partait du premier
echantillon audio. Sur une source dont l'audio commence a 2,98 s, le WAV
faisait 27,03 s et un son de 4,0 s tombait a 1,02 s — accepte sans un mot,
donc sous-titres, coupes et clips decales d'autant.
"""
import hashlib
import math
import struct
import subprocess
import wave
from pathlib import Path

import pytest

from clipforge.pipeline.audio_extract import AudioExtractError, extract_wav

DUREE_S = 12.0
SR_SOURCE = 48000
BIPS_S = (4.0, 8.0)
RETARD_S = 2.98
SEUIL_RMS = 0.05
FENETRE_S = 0.010
TOLERANCE_S = 0.02


def _ecrire_bips(chemin: Path, depuis_s: float) -> None:
    """Silence et bips de 150 ms, les bips gardant leurs instants ABSOLUS."""
    total = round((DUREE_S - depuis_s) * SR_SOURCE)
    ech = [0.0] * total
    for bip in BIPS_S:
        depart = round((bip - depuis_s) * SR_SOURCE)
        n, fondu = int(0.150 * SR_SOURCE), int(0.005 * SR_SOURCE)
        for i in range(n):
            if not 0 <= depart + i < total:
                continue
            gain = min(1.0, i / fondu, (n - i) / fondu)
            ech[depart + i] = 0.6 * gain * math.sin(2 * math.pi * 1000.0 * i / SR_SOURCE)
    with wave.open(str(chemin), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR_SOURCE)
        w.writeframes(b"".join(struct.pack("<h", int(v * 32767)) for v in ech))


def _fabriquer(dossier: Path, nom: str, retard_s: float) -> Path:
    """Source de 12 s dont la piste audio demarre a `retard_s`."""
    tampon = dossier / f"_{nom}.wav"
    _ecrire_bips(tampon, retard_s)
    entree = ["-i", str(tampon)] if retard_s == 0.0 else [
        "-itsoffset", str(retard_s), "-i", str(tampon)]
    source = dossier / f"{nom}.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error",
         "-f", "lavfi", "-i", f"testsrc=size=160x120:rate=15:duration={DUREE_S}",
         *entree, "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
         "-shortest", str(source)], check=True, capture_output=True)
    return source


def _bips(wav: Path) -> tuple[list[float], float]:
    with wave.open(str(wav)) as w:
        brut, sr = w.readframes(w.getnframes()), w.getframerate()
    ech = [v / 32768.0 for v in struct.unpack(f"<{len(brut) // 2}h", brut)]
    pas = int(FENETRE_S * sr)
    trouves, actif = [], False
    for i in range(0, len(ech) - pas, pas):
        bloc = ech[i:i + pas]
        fort = (sum(v * v for v in bloc) / len(bloc)) ** 0.5 >= SEUIL_RMS
        if fort and not actif:
            trouves.append(round(i / sr, 3))
        actif = fort
    return trouves, round(len(ech) / sr, 3)


@pytest.mark.integration
def test_le_detecteur_de_bips_sait_distinguer_les_deux_cas(tmp_path):
    """Un instrument qui repondrait la meme chose aux deux cas ne prouverait
    rien des tests qui suivent."""
    _ecrire_bips(tmp_path / "a.wav", 0.0)
    _ecrire_bips(tmp_path / "b.wav", RETARD_S)
    assert _bips(tmp_path / "a.wav")[0] == [4.0, 8.0]
    assert _bips(tmp_path / "b.wav")[0] == [round(b - RETARD_S, 3) for b in BIPS_S]


@pytest.mark.integration
def test_vod_a_audio_tardif_recalee_sur_la_source(tmp_path):
    source = _fabriquer(tmp_path, "tardif", RETARD_S)
    trouves, duree = _bips(extract_wav(source, tmp_path / "tardif.wav"))
    assert duree == pytest.approx(DUREE_S, abs=0.05)
    assert trouves == pytest.approx(list(BIPS_S), abs=TOLERANCE_S)


@pytest.mark.integration
def test_vod_a_l_heure_rendue_a_l_identique(tmp_path):
    """Le correctif ne doit pas payer son du sur les sources saines : meme
    commande, donc meme WAV a l'echantillon."""
    source = _fabriquer(tmp_path, "plein", 0.0)
    par_le_code = extract_wav(source, tmp_path / "plein.wav")
    temoin = tmp_path / "temoin.wav"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(source), "-vn",
                    "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", "-f", "wav",
                    str(temoin)], check=True, capture_output=True)
    assert (hashlib.sha256(par_le_code.read_bytes()).digest()
            == hashlib.sha256(temoin.read_bytes()).digest())
    assert _bips(par_le_code)[0] == pytest.approx(list(BIPS_S), abs=TOLERANCE_S)


@pytest.mark.integration
def test_fenetre_ouverte_avant_l_audio_refusee_plutot_que_fausse(tmp_path):
    source = _fabriquer(tmp_path, "tardif", RETARD_S)
    dest = tmp_path / "clip.wav"
    with pytest.raises(AudioExtractError, match="decale"):
        extract_wav(source, dest, start_s=0.0, end_s=6.0)
    assert not dest.is_file()


@pytest.mark.integration
def test_fenetre_ouverte_apres_l_audio_reste_juste(tmp_path):
    source = _fabriquer(tmp_path, "tardif", RETARD_S)
    wav = extract_wav(source, tmp_path / "clip.wav", start_s=3.5, end_s=9.0)
    trouves, _ = _bips(wav)
    assert trouves == pytest.approx([b - 3.5 for b in BIPS_S], abs=TOLERANCE_S)
