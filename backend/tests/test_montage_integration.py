"""Anneau 2 (Task 11) : rendus REELS sur des sources synthetiques, mesures avec
ffprobe/ffmpeg via subprocess Python -- jamais l'espion FauxFfmpeg de faux_ffmpeg.py.

Ce que les anneaux 0/1 (unitaires, filter_complex en chaine de caracteres) ne
peuvent structurellement pas voir : une expression zoompan syntaxiquement
fausse (ffmpeg seul la refuse), un fichier de sortie dont la duree ou la
loudness reelles divergent du plan, un teaser qui rend une frame noire, un
audio coupe qui survit a l'octet pres a l'encodage/decodage.

Toutes les sources sont construites ici (testsrc2 + WAV ecrit a la main),
jamais data/ ; toutes les sorties vont sous tmp_path/tmp_path_factory.
"""
import json
import re
import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pytest
from test_render_queue import PROFILE, _clip, _rendre, _seed

from clipforge import db as dbmod
from clipforge.pipeline.audio_analysis import read_wav_mono
from clipforge.pipeline.probe import probe_media
from clipforge.pipeline.segmenter import Word
from clipforge.render import montage_plan as montage_plan_mod
from clipforge.render import queue as queue_mod
from clipforge.render import sfx as sfx_mod
from clipforge.render.montage.coldopen import choose_cold_open
from clipforge.render.montage.edl import build_edl
from clipforge.render.montage.energy import fine_energy
from clipforge.render.montage.timemap import TimeMap
from clipforge.render.montage.words_out import relative_words
from clipforge.render.queue import render_clip_job

pytestmark = pytest.mark.integration

_LOUDNORM_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)
_TEASER_INPUT_RE = re.compile(r"\[(\d+):v\]trim=end=([\d.]+),[^;]*\[vc0\]")

# --------------------------------------------------------------------------
# Construction de sources synthetiques (jamais data/, jamais de hasard)
# --------------------------------------------------------------------------


def _write_tone_wav(path: Path, *, duration_s: float,
                    segments: list[tuple[float, float, float, float]],
                    sr: int = 48000, channels: int = 1) -> Path:
    """WAV pcm_s16le (mono ou stereo) : silence NUMERIQUE partout, sauf
    `segments` (debut_s, fin_s, freq_hz, amplitude) ou une sinusoide pure joue.

    Aucun ffmpeg ici : echantillons ecrits a la main (numpy), deterministes,
    condition du test de correspondance audio (case 5) -- une extraction/un
    encodage reel introduirait du bruit qu'on ne controlerait plus.

    `channels=2` (meme signal duplique sur les deux canaux) : sans ca, la
    source reste mono de bout en bout (aucune branche SFX pour forcer amix a
    la stereo, cf. sfx.py::_mix_graph) -- necessaire pour que la sortie
    mesuree soit reellement stereo (case 1).
    """
    n = round(duration_s * sr)
    t = np.arange(n) / sr
    x = np.zeros(n, dtype=np.float64)
    for start_s, end_s, freq_hz, amplitude in segments:
        mask = (t >= start_s) & (t < end_s)
        x[mask] = amplitude * np.sin(2 * np.pi * freq_hz * t[mask])
    pcm_mono = np.clip(x * 32767.0, -32768, 32767).astype(np.int16)
    pcm = np.repeat(pcm_mono, channels) if channels > 1 else pcm_mono
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())
    return path


def _mux_video_with_audio(video_out: Path, audio_wav: Path, *, duration_s: float,
                          width: int = 1920, height: int = 1080, fps: int = 30) -> Path:
    """Motif visuel synthetique (testsrc2, jamais noir) muxe avec un WAV construit
    a la main. Source de test complete (video+audio) pour render_clip_job."""
    cmd = [
        "ffmpeg", "-y", "-f", "lavfi",
        "-i", f"testsrc2=size={width}x{height}:rate={fps}:duration={duration_s}",
        "-i", str(audio_wav),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "28", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest", str(video_out),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", check=False)
    assert proc.returncode == 0, (
        f"construction source {video_out} echouee (code {proc.returncode}) : "
        f"{proc.stderr[-800:]}")
    return video_out


def _seed_source_clip(db_path: Path, *, source: Path, width: int, height: int, fps: float,
                      words: list[tuple[float, float, str]], start_s: float, end_s: float,
                      montage: str, hook: str | None = None, emoji: str | None = None,
                      signals: list[tuple[float, float]] | None = None) -> int:
    """VOD + profil + clip prets a rendre sur une source construite par ce module."""
    conn = dbmod.get_db(db_path)
    try:
        vod_id = dbmod.create_vod(conn, title="vod integration", source_path=str(source))
        conn.execute("UPDATE vods SET width=?, height=?, fps=? WHERE id=?",
                     (width, height, fps, vod_id))
        conn.executemany(
            "INSERT INTO words (vod_id, idx, start_s, end_s, text, probability) "
            "VALUES (?,?,?,?,?,?)",
            [(vod_id, i, s, e, txt, 0.9) for i, (s, e, txt) in enumerate(words)])
        if signals:
            conn.executemany(
                "INSERT INTO signals (vod_id, kind, t_s, score) VALUES (?,?,?,?)",
                [(vod_id, "audio", float(t), float(score)) for t, score in signals])
        cur = conn.execute(
            "INSERT INTO scene_profiles (name, cam_x, cam_y, cam_w, cam_h, "
            "game_x, game_y, game_w, game_h) VALUES ('scene',?,?,?,?,?,?,?,?)", PROFILE)
        profile_id = cur.lastrowid
        cur = conn.execute(
            "INSERT INTO clips (vod_id, start_s, end_s, title, profile_id, origin, hook, emoji) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (vod_id, start_s, end_s, "clip integration", profile_id,
             "auto" if hook else "user", hook, emoji))
        clip_id = cur.lastrowid
        dbmod.set_setting(conn, "montage", montage)
        conn.commit()
        return clip_id
    finally:
        conn.close()


def _rendre_source(tmp_path: Path, clip_id: int) -> None:
    render_clip_job(clip_id, str(tmp_path / "clipforge.db"), str(tmp_path / "jobs"),
                    str(tmp_path / "output"))


def _clip_row(tmp_path: Path, clip_id: int):
    conn = dbmod.get_db(tmp_path / "clipforge.db")
    try:
        return conn.execute("SELECT * FROM clips WHERE id=?", (clip_id,)).fetchone()
    finally:
        conn.close()


# --------------------------------------------------------------------------
# Instruments de mesure (subprocess Python uniquement, jamais de shell brut)
# --------------------------------------------------------------------------


def _audio_stream(path: Path) -> dict:
    """codec/canaux/frequence de la 1ere piste audio. "-of json" (pas
    "-print_format") : evite le mot isole "format" dans la commande."""
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0",
         "-show_entries", "stream=codec_name,channels,sample_rate",
         "-of", "json", str(path)],
        capture_output=True, text=True, encoding="utf-8", check=True)
    return json.loads(proc.stdout)["streams"][0]


def _measured_lufs(path: Path) -> float:
    """Loudness integree (I, LUFS) mesuree par une passe loudnorm ffmpeg sur le
    fichier FINI -- verification externe, independante de measure_loudness
    (l'auto-declaration du pipeline) : ce qui compte ici, c'est ce que le
    fichier livre contient reellement."""
    cmd = ["ffmpeg", "-i", str(path), "-af",
           "loudnorm=I=-14:TP=-1:LRA=11:print_format=json", "-f", "null", "-"]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", check=False)
    assert proc.returncode == 0, (
        f"mesure loudness {path} echouee (code {proc.returncode}) : {proc.stderr[-500:]}")
    match = _LOUDNORM_JSON_RE.search(proc.stderr)
    assert match, f"mesure loudness {path} : pas de bloc JSON dans la sortie loudnorm ffmpeg"
    data = json.loads(match.group(0))
    return float(data["input_i"])


def _loudnorm_json(path: Path, expr: str) -> dict:
    """Bloc JSON qu'une passe `expr` (loudnorm, print_format=json) ecrit sur `path`."""
    proc = subprocess.run(["ffmpeg", "-i", str(path), "-af", expr, "-f", "null", "-"],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", check=False)
    assert proc.returncode == 0, f"passe loudnorm {path} echouee : {proc.stderr[-500:]}"
    match = _LOUDNORM_JSON_RE.search(proc.stderr)
    assert match, f"passe loudnorm {path} : pas de bloc JSON"
    return json.loads(match.group(0))


def _loudnorm_lineaire(mesure: dict) -> bool:
    """Vrai si la seconde passe de loudnorm, nourrie de `mesure`, reste en mode
    lineaire. Condition de libavfilter/af_loudnorm.c pour la cible du rendu
    (I=-14, TP=-1, LRA=11) : une mesure sans valeur sentinelle (TP 99, seuil
    -70, LRA 0, I 0), TP mesure + (I cible - I mesure) <= TP cible et LRA
    mesure <= LRA cible. Sinon le filtre repasse de lui-meme en dynamique."""
    i, tp, lra, seuil = (float(mesure[k])
                         for k in ("input_i", "input_tp", "input_lra", "input_thresh"))
    exploitable = tp != 99 and seuil != -70 and lra != 0 and i != 0
    return exploitable and tp + (-14.0 - i) <= -1.0 and lra <= 11.0


def _extract_audio_window(source: Path, dest: Path, *, start_s: float, duration_s: float,
                          sr: int = 48000) -> Path:
    """Sous-fenetre audio de `source` en WAV mono, meme chaine que la production
    (extract_wav) : -ss avant -i, downmix mono, resample explicite."""
    cmd = ["ffmpeg", "-y", "-ss", f"{start_s:.3f}", "-t", f"{duration_s:.3f}",
           "-i", str(source), "-vn", "-ac", "1", "-ar", str(sr),
           "-c:a", "pcm_s16le", str(dest)]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", check=False)
    assert proc.returncode == 0, (
        f"extraction fenetre audio {source}[{start_s:.3f},{start_s + duration_s:.3f}] "
        f"echouee (code {proc.returncode}) : {proc.stderr[-500:]}")
    return dest


def _dominant_frequency_hz(samples: np.ndarray, sr: int, *, min_hz: float = 50.0) -> float:
    """Frequence du pic FFT le plus fort (fenetre de Hanning), en excluant le
    grave (`min_hz`) pour ignorer un residu DC/rumble."""
    assert len(samples) > 1, "fenetre audio vide : impossible de mesurer une frequence"
    fenetre = samples.astype(np.float64) * np.hanning(len(samples))
    freqs = np.fft.rfftfreq(len(fenetre), d=1.0 / sr)
    mag = np.abs(np.fft.rfft(fenetre))
    mag[freqs < min_hz] = 0.0
    return float(freqs[int(np.argmax(mag))])


def _frame_not_black(path: Path, *, at_s: float, threshold: float = 8.0) -> tuple[bool, float]:
    """(non_noire, luminance_moyenne) de la frame extraite a `at_s`."""
    cmd = ["ffmpeg", "-y", "-ss", f"{at_s:.3f}", "-i", str(path), "-frames:v", "1",
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    proc = subprocess.run(cmd, capture_output=True, check=False)
    assert proc.returncode == 0, (
        f"extraction frame {path}@{at_s}s echouee (code {proc.returncode}) : "
        f"{proc.stderr[-500:].decode('utf-8', 'replace')}")
    frame = np.frombuffer(proc.stdout, dtype=np.uint8)
    assert frame.size > 0, f"{path}@{at_s}s : aucune donnee de frame recue"
    mean = float(frame.mean())
    return mean > threshold, mean


# --------------------------------------------------------------------------
# Validation des instruments : un cas connu-bon ET un cas connu-mauvais
# chacun, AVANT de leur faire confiance dans les tests de rendu ci-dessous.
# --------------------------------------------------------------------------


def test_probe_media_duration_helper_distinguishes_known_durations(tmp_path):
    court = tmp_path / "court.mp4"
    long = tmp_path / "long.mp4"
    for path, dur in ((court, 2.0), (long, 5.0)):
        proc = subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi", "-i",
             f"color=c=blue:size=64x64:rate=10:duration={dur}",
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "30",
             "-pix_fmt", "yuv420p", str(path)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
        assert proc.returncode == 0, f"construction {path} echouee : {proc.stderr[-500:]}"
    info_court = probe_media(court)
    info_long = probe_media(long)
    assert abs(info_court.duration_s - 2.0) <= 0.1, f"{court}: mesure {info_court.duration_s}s"
    assert abs(info_long.duration_s - 5.0) <= 0.1, f"{long}: mesure {info_long.duration_s}s"
    assert info_long.duration_s - info_court.duration_s > 1.0, (
        f"l'instrument doit distinguer deux durees differentes : "
        f"{court}={info_court.duration_s}s, {long}={info_long.duration_s}s")


def test_measured_lufs_helper_distinguishes_loud_from_quiet(tmp_path):
    fort = _write_tone_wav(tmp_path / "fort.wav", duration_s=3.0,
                           segments=[(0.0, 3.0, 440.0, 0.9)])
    faible = _write_tone_wav(tmp_path / "faible.wav", duration_s=3.0,
                             segments=[(0.0, 3.0, 440.0, 0.02)])
    lufs_fort = _measured_lufs(fort)
    lufs_faible = _measured_lufs(faible)
    assert lufs_fort - lufs_faible > 15.0, (
        f"l'instrument doit distinguer un son fort d'un son faible : "
        f"{fort}={lufs_fort} LUFS, {faible}={lufs_faible} LUFS (ecart attendu > 15)")


def test_loudnorm_linear_helper_agrees_with_loudnorm_own_verdict(tmp_path):
    """_loudnorm_lineaire contre le verdict de loudnorm lui-meme
    (normalization_type de la seconde passe, expression de production) : deux
    paliers a 0.5 puis 0.3 restent lineaires ; un ton plat (LRA 0, valeur
    sentinelle) et un ton a 0.05 perce d'une salve de 30 ms a 0.95 (TP - I au
    dela de 13 dB) repassent en dynamique."""
    cas = (("paliers", [(0.0, 5.0, 440.0, 0.5), (5.0, 10.0, 440.0, 0.3)]),
           ("plat", [(0.0, 10.0, 440.0, 0.5)]),
           ("salve", [(0.0, 10.0, 440.0, 0.05), (4.9, 4.93, 440.0, 0.95)]))
    verdicts = []
    for nom, segments in cas:
        wav = _write_tone_wav(tmp_path / f"{nom}.wav", duration_s=10.0, segments=segments)
        mesure = _loudnorm_json(wav, sfx_mod._loudnorm_expr(None, json_output=True))
        seconde = _loudnorm_json(wav, sfx_mod._loudnorm_expr(mesure, json_output=True))
        verdicts.append((_loudnorm_lineaire(mesure), seconde["normalization_type"]))
    assert verdicts == [(True, "linear"), (False, "dynamic"), (False, "dynamic")], verdicts


def test_dominant_frequency_helper_distinguishes_known_tones(tmp_path):
    """Valide l'instrument central du test de correspondance audio (case 5) : un
    ton connu a 440 Hz ET un ton connu a 1200 Hz, extraits par la MEME chaine
    ffmpeg que le test de contenu (_extract_audio_window). Un instrument qui
    rendrait la meme frequence pour les deux serait invisible dans ce test tout
    en etant casse -- exactement le risque que ce projet a deja subi deux fois."""
    a = _write_tone_wav(tmp_path / "a.wav", duration_s=1.0, segments=[(0.0, 1.0, 440.0, 0.7)])
    b = _write_tone_wav(tmp_path / "b.wav", duration_s=1.0, segments=[(0.0, 1.0, 1200.0, 0.7)])
    fen_a = _extract_audio_window(a, tmp_path / "fen_a.wav", start_s=0.2, duration_s=0.5)
    fen_b = _extract_audio_window(b, tmp_path / "fen_b.wav", start_s=0.2, duration_s=0.5)
    freq_a = _dominant_frequency_hz(*read_wav_mono(fen_a))
    freq_b = _dominant_frequency_hz(*read_wav_mono(fen_b))
    assert abs(freq_a - 440.0) <= 15.0, f"{fen_a}: attendu ~440 Hz, mesure {freq_a} Hz"
    assert abs(freq_b - 1200.0) <= 15.0, f"{fen_b}: attendu ~1200 Hz, mesure {freq_b} Hz"
    assert abs(freq_a - freq_b) > 100.0, (
        f"l'instrument doit distinguer deux frequences differentes : "
        f"{fen_a}={freq_a} Hz, {fen_b}={freq_b} Hz")


def test_frame_not_black_helper_distinguishes_black_from_colorful(tmp_path):
    noire = tmp_path / "noire.mp4"
    coloree = tmp_path / "coloree.mp4"
    specs = [(noire, "color=c=black:size=64x64:rate=10:duration=1"),
             (coloree, "testsrc2=size=64x64:rate=10:duration=1")]
    for path, lavfi in specs:
        proc = subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi", "-i", lavfi, "-c:v", "libx264",
             "-preset", "veryfast", "-crf", "30", "-pix_fmt", "yuv420p", str(path)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
        assert proc.returncode == 0, f"construction {path} echouee : {proc.stderr[-500:]}"
    non_noire_n, moyenne_n = _frame_not_black(noire, at_s=0.3)
    non_noire_c, moyenne_c = _frame_not_black(coloree, at_s=0.3)
    assert not non_noire_n, f"{noire}@0.3s : attendu noir, luminance moyenne {moyenne_n}"
    assert non_noire_c, f"{coloree}@0.3s : attendu non noir, luminance moyenne {moyenne_c}"
    assert moyenne_c - moyenne_n > 10.0, (
        f"l'instrument doit distinguer une frame noire d'une frame coloree : "
        f"noire={moyenne_n}, coloree={moyenne_c}")


def test_audio_stream_helper_distinguishes_stereo_from_mono(tmp_path):
    """Valide `_audio_stream` (rapport Task 11 : seul instrument sans cas
    connu-bon/connu-mauvais) : un WAV stereo et un WAV mono, memes frequence
    d'echantillonnage et contenu, doivent rendre des comptes de canaux
    differents -- un instrument qui rendrait le meme nombre pour les deux
    serait invisible tout en etant casse."""
    stereo = _write_tone_wav(tmp_path / "stereo.wav", duration_s=1.0,
                             segments=[(0.0, 1.0, 440.0, 0.5)], channels=2)
    mono = _write_tone_wav(tmp_path / "mono.wav", duration_s=1.0,
                           segments=[(0.0, 1.0, 440.0, 0.5)], channels=1)
    info_stereo = _audio_stream(stereo)
    info_mono = _audio_stream(mono)
    assert info_stereo["channels"] == 2, f"{stereo}: flux audio mesure = {info_stereo}"
    assert info_mono["channels"] == 1, f"{mono}: flux audio mesure = {info_mono}"
    assert info_stereo["sample_rate"] == info_mono["sample_rate"] == "48000", (
        f"l'instrument doit lire la meme frequence d'echantillonnage pour deux "
        f"fichiers a 48000 Hz : {info_stereo} vs {info_mono}")


# --------------------------------------------------------------------------
# Case 1 : clip coupe (deux trous coupables, tons position-encodes), rendu +
# mesure de duree/loudness. Case 5 (correspondance audio/EDL) a sa propre
# fixture dediee plus bas, avec un vrai seek d'entree (ce qu'il garde et dans
# quels modes de loudnorm : cf. section Case 5).
# --------------------------------------------------------------------------

GAP_DUR_S = 12.0
# (offset_s, freq_hz) : chaque seconde active de la source encode sa propre
# frequence -- l'ADN du test de correspondance audio (case 5).
GAP_ACTIVE_SECONDS = [(0, 300.0), (1, 500.0), (2, 700.0), (3, 900.0),
                      (6, 1100.0), (7, 1300.0), (10, 1500.0), (11, 1700.0)]
GAP_TONE_SEGMENTS = [(float(i), float(i) + 1.0, freq, 0.5) for i, freq in GAP_ACTIVE_SECONDS]
# 3 mots-zones bornant exactement le ton (silence numerique ailleurs) : deux
# trous muets [4.0,6.0) et [8.0,10.0) de 2 s chacun, largement au-dessus du
# seuil de coupe "standard" (0.55 s) et bien plus silencieux que la mediane.
GAP_WORDS_ABS = [(0.0, 4.0, "un"), (6.0, 8.0, "deux"), (10.0, 12.0, "trois")]


@dataclass
class _RenderedGapClip:
    out_path: Path
    tmap: TimeMap


@pytest.fixture(scope="module")
def gap_source(tmp_path_factory) -> Path:
    d = tmp_path_factory.mktemp("gap_src")
    audio = _write_tone_wav(d / "gap_tone.wav", duration_s=GAP_DUR_S, segments=GAP_TONE_SEGMENTS,
                            channels=2)
    return _mux_video_with_audio(d / "gap_source.mp4", audio, duration_s=GAP_DUR_S)


@pytest.fixture(scope="module")
def rendered_gap_clip(gap_source, tmp_path_factory) -> _RenderedGapClip:
    root = tmp_path_factory.mktemp("gap_render")
    db_path = root / "clipforge.db"
    clip_id = _seed_source_clip(db_path, source=gap_source, width=1920, height=1080, fps=30.0,
                                words=GAP_WORDS_ABS, start_s=0.0, end_s=GAP_DUR_S, montage="1")
    render_clip_job(clip_id, str(db_path), str(root / "jobs"), str(root / "output"))
    conn = dbmod.get_db(db_path)
    try:
        clip = conn.execute("SELECT * FROM clips WHERE id=?", (clip_id,)).fetchone()
    finally:
        conn.close()
    assert clip["status"] == "done", f"rendu du clip a trous echoue : {clip['error']}"

    # Reconstruction INDEPENDANTE de l'EDL "attendu" -- memes primitives que le
    # pipeline (build_edl/TimeMap), sur l'audio REELLEMENT extrait par ce meme
    # rendu (job_dir/clip_audio.wav), jamais un nombre fige a la main.
    wav = root / "jobs" / f"clip{clip_id}" / montage_plan_mod.CLIP_AUDIO_NAME
    samples, sr = read_wav_mono(wav)
    energy = fine_energy(samples, sr)
    # start_s=0 => deja relatifs
    mots_rel = [Word(start=s, end=e, text=t) for s, e, t in GAP_WORDS_ABS]
    edl = build_edl(mots_rel, energy, clip_dur_s=GAP_DUR_S, cut_style="standard")
    assert len(edl.segments) > 1, (
        "le clip de test doit produire une vraie coupe (2 trous silencieux attendus), "
        f"segments obtenus : {edl.segments}")
    tmap = TimeMap.from_edl(edl)
    return _RenderedGapClip(out_path=Path(clip["output_path"]), tmap=tmap)


def test_cut_clip_renders_and_measures_right(rendered_gap_clip):
    """Case 1 : un clip coupe (2 trous muets) rend, et la mesure du fichier fini
    correspond au plan -- duree, canaux/frequence audio, loudness integree."""
    out = rendered_gap_clip.out_path
    expected = rendered_gap_clip.tmap.output_duration
    info = probe_media(out)
    ecart = abs(info.duration_s - expected)
    assert ecart <= 0.1, (
        f"{out}: duree mesuree {info.duration_s}s, attendue {expected}s (arithmetique EDL), "
        f"ecart {ecart:.3f}s")

    audio = _audio_stream(out)
    assert audio["channels"] == 2, f"{out}: flux audio mesure = {audio}"
    assert audio["sample_rate"] == "48000", f"{out}: flux audio mesure = {audio}"

    lufs = _measured_lufs(out)
    assert -15.0 <= lufs <= -13.0, (
        f"{out}: loudness integree mesuree {lufs} LUFS, attendu [-15,-13]")


# --------------------------------------------------------------------------
# Case 5 (fixture dediee) : source PLUS LONGUE que le clip, coupe reelle en
# ENTREE (-ss 8 -t 16). gap_source (ci-dessus) a start_s=0.0 sur une source
# qui dure exactement la duree du clip : -ss 0.0 n'est PAS un vrai seek, et
# le seek d'entree est une condition de la regression Task 7/10 (DTS non
# monotone apres concat, degrade silencieusement par loudnorm en aval).
# Recette du controleur : source 30 s, clip [8,24] (dur 16 s), ZERO effet
# sonore (aucun hook, aucun signal -> aucune fenetre de punch, aucun teaser
# choisi -- un seul evenement SFX suffirait a masquer le defaut, cf.
# sfx.py::_mix_graph, branche amix qui re-cale le flux), au moins 2 segments
# gardes (vrai concat).
#
# L'autre condition est loudnorm en mode DYNAMIQUE. Revue finale V6 : c'est
# le mode de production des que measure_loudness rend None (repli documente),
# et de lui-meme sur de vrais clips (les 5 de la lecture sur VOD de Task 12
# sortent de la condition du mode lineaire : LRA > 11 ou TP - I > 13 dB).
# Les tons plats a 0.5 de seek_source, eux, gardent loudnorm lineaire, et le
# test ne pouvait rien voir (fix round 1 de Task 11). Il tourne donc dans les
# trois modes, chacun verifie sur la mesure reelle du rendu. Mesure sans
# l'aresample pre-loudnorm (ffmpeg 9.0.1) : lineaire, 12 secondes justes sur
# 12 ; sans mesure, et dynamique de lui-meme (tons a 0.05 et SEEK_SALVE :
# I -23.04, TP -0.41), 3 secondes fausses sur 12 (la seconde 0 joue la 1,
# les 1 et 2 sortent vides), 87 avertissements "Non-monotonic DTS", rc=0,
# status done.
# --------------------------------------------------------------------------

SEEK_SOURCE_DUR_S = 30.0
SEEK_CLIP_START_S = 8.0
SEEK_CLIP_END_S = 24.0
SEEK_CLIP_DUR_S = SEEK_CLIP_END_S - SEEK_CLIP_START_S  # 16.0 -- vrai -ss 8 -t 16
# (offset_s relatif au CLIP, freq_hz) -- meme ADN que GAP_ACTIVE_SECONDS :
# chaque seconde active encode sa propre frequence. Les 3 mots ci-dessous
# remplissent ENTIEREMENT chaque zone active (aucun silence cache dans un
# mot) pour que le pic d'energie ne se detache jamais nettement de la
# mediane -- sinon un teaser serait choisi (verifie par le test lui-meme).
SEEK_ACTIVE_SECONDS = [(0, 300.0), (1, 500.0), (2, 700.0), (3, 900.0), (4, 1100.0),
                       (7, 1300.0), (8, 1500.0), (9, 1700.0), (10, 1900.0),
                       (13, 2100.0), (14, 2300.0), (15, 2500.0)]
# Mots RELATIFS au clip -- deux vrais trous muets [5,7) et [11,13), 2 s
# chacun, au moins 2 segments gardes une fois coupes.
SEEK_WORDS_REL = [(0.0, 5.0, "un"), (7.0, 11.0, "deux"), (13.0, 16.0, "trois")]


# (debut, fin, freq, amplitude) relatifs au clip : 30 ms a 0.95 dans la seconde
# 1, hors de sa fenetre de mesure [1.25, 1.75] et avant TEASER_MIN_PEAK_T (le
# pic ne peut pas donner de teaser).
SEEK_SALVE = (1.85, 1.88, 500.0, 0.95)


def _seek_source(d: Path, *, amplitude: float,
                 salves: list[tuple[float, float, float, float]]) -> Path:
    segments = [(SEEK_CLIP_START_S + off, SEEK_CLIP_START_S + off + 1.0, freq, amplitude)
                for off, freq in SEEK_ACTIVE_SECONDS]
    segments += [(SEEK_CLIP_START_S + a, SEEK_CLIP_START_S + b, freq, amp)
                 for a, b, freq, amp in salves]
    audio = _write_tone_wav(d / "seek_tone.wav", duration_s=SEEK_SOURCE_DUR_S,
                            segments=segments, channels=2)
    return _mux_video_with_audio(d / "seek_source.mp4", audio, duration_s=SEEK_SOURCE_DUR_S)


@pytest.fixture(scope="module")
def seek_source(tmp_path_factory) -> Path:
    return _seek_source(tmp_path_factory.mktemp("seek_src"), amplitude=0.5, salves=[])


@pytest.fixture(scope="module")
def seek_source_salve(tmp_path_factory) -> Path:
    return _seek_source(tmp_path_factory.mktemp("seek_salve_src"), amplitude=0.05,
                        salves=[SEEK_SALVE])


@pytest.mark.parametrize("source, loudnorm", [("seek_source", "lineaire"),
                                              ("seek_source", "sans_mesure"),
                                              ("seek_source_salve", "dynamique")])
def test_cut_clip_audio_content_matches_the_kept_segments(tmp_path, request, monkeypatch,
                                                          source, loudnorm):
    """Verifie que l'audio qui SURVIT dans la sortie correspond au CONTENU des
    segments gardes par l'EDL, pas seulement a la longueur totale -- sur un
    rendu avec un VRAI seek d'entree (-ss 8 -t 16, source de 30 s) et un vrai
    concat (>= 2 segments gardes), condition plus exigeante que gap_source
    (start_s=0.0, aucun seek reel).

    Chaque seconde active de la source encode sa PROPRE frequence (cf.
    SEEK_ACTIVE_SECONDS) ; on verifie ici que la frequence dominante mesuree
    en sortie, a l'instant OU l'EDL dit que ce contenu doit se trouver, est
    la bonne -- une troncature ou un reordonnancement de l'audio ferait
    echouer au moins un de ces points, meme si la duree totale restait juste
    (ce qu'une verification de seule duree ne verrait pas).

    Garde de la regression Task 7/10 (un concat de segments coupes suivi de
    loudnorm sur une entree seekee tronque et reordonne l'audio, rc=0) et de
    son correctif, l'aresample avant loudnorm sur le chemin coupe
    (sfx.py::_mix_graph). Sans lui, les variantes sans_mesure et dynamique
    echouent (3 secondes fausses sur 12, cf. section Case 5) ; la variante
    lineaire passe avec ou sans, elle tient le contenu dans le mode nominal.
    Chaque variante verifie d'abord que loudnorm est bien dans son mode.
    """
    mesures: list[dict | None] = []
    mesure_reelle = montage_plan_mod.measure_loudness

    def _mesure(*args, **kwargs):
        mesure = None if loudnorm == "sans_mesure" else mesure_reelle(*args, **kwargs)
        mesures.append(mesure)
        return mesure

    monkeypatch.setattr(montage_plan_mod, "measure_loudness", _mesure)
    source_path = request.getfixturevalue(source)
    db_path = tmp_path / "clipforge.db"
    words_abs = [(SEEK_CLIP_START_S + s, SEEK_CLIP_START_S + e, txt)
                for s, e, txt in SEEK_WORDS_REL]
    clip_id = _seed_source_clip(db_path, source=source_path, width=1920, height=1080, fps=30.0,
                                words=words_abs, start_s=SEEK_CLIP_START_S, end_s=SEEK_CLIP_END_S,
                                montage="1")
    _rendre_source(tmp_path, clip_id)
    clip = _clip_row(tmp_path, clip_id)
    assert clip["status"] == "done", f"rendu du clip seeke echoue : {clip['error']}"
    assert len(mesures) == 1, mesures
    if loudnorm == "sans_mesure":
        assert mesures[0] is None
    else:
        assert mesures[0] is not None, "la mesure de loudness du rendu a echoue"
        assert _loudnorm_lineaire(mesures[0]) == (loudnorm == "lineaire"), (
            f"le scenario devait mener loudnorm en mode {loudnorm} : mesure {mesures[0]}")

    # Reconstruction INDEPENDANTE de l'EDL "attendu" -- memes primitives que le
    # pipeline (relative_words/build_edl/TimeMap), sur l'audio REELLEMENT
    # extrait par ce meme rendu (job_dir/clip_audio.wav), jamais un nombre
    # fige a la main.
    wav = tmp_path / "jobs" / f"clip{clip_id}" / montage_plan_mod.CLIP_AUDIO_NAME
    samples, sr = read_wav_mono(wav)
    energy = fine_energy(samples, sr)
    mots_abs = [Word(start=s, end=e, text=t) for s, e, t in words_abs]
    mots_rel = relative_words(mots_abs, SEEK_CLIP_START_S)
    edl = build_edl(mots_rel, energy, clip_dur_s=SEEK_CLIP_DUR_S, cut_style="standard")
    assert len(edl.segments) > 1, (
        "le clip de test doit produire un vrai concat (au moins 2 segments gardes), "
        f"segments obtenus : {edl.segments}")
    assert edl.teaser is None, (
        "le clip de test doit rester sans teaser (un whoosh SFX ajouterait une "
        f"branche amix, cf. entete de section) -- teaser obtenu : {edl.teaser}")
    tmap = TimeMap.from_edl(edl)
    out = Path(clip["output_path"])

    resultats = []
    for offset, freq in SEEK_ACTIVE_SECONDS:
        src_lo, src_hi = offset + 0.25, offset + 0.75  # marge de securite loin des bords coupes
        out_lo, out_hi = tmap.to_output(src_lo), tmap.to_output(src_hi)
        assert out_lo is not None and out_hi is not None, (
            f"fenetre source [{src_lo},{src_hi}]s (attendue {freq} Hz) hors des segments "
            f"gardes par l'EDL -- verifier la construction du clip de test")
        fenetre = tmp_path / f"fenetre_{offset}s.wav"
        _extract_audio_window(out, fenetre, start_s=out_lo, duration_s=out_hi - out_lo)
        mesuree = _dominant_frequency_hz(*read_wav_mono(fenetre))
        resultats.append((offset, freq, out_lo, out_hi, mesuree))

    for offset, freq, out_lo, out_hi, mesuree in resultats:
        assert abs(mesuree - freq) <= 20.0, (
            f"{out}: seconde source {offset}-{offset + 1}s (mappee en sortie sur "
            f"[{out_lo:.3f},{out_hi:.3f}]s) attendait {freq} Hz, frequence dominante mesuree "
            f"{mesuree:.1f} Hz -- audio tronque ou reordonne, "
            f"toutes les mesures : {resultats}")


def test_montage_off_renders_the_source_duration(tmp_path, gap_source):
    """Case 2 : meme source, montage=0 -- aucune coupe, la sortie dure la duree
    du clip source, pas celle (plus courte) que produirait le moteur V6."""
    db_path = tmp_path / "clipforge.db"
    clip_id = _seed_source_clip(db_path, source=gap_source, width=1920, height=1080, fps=30.0,
                                words=GAP_WORDS_ABS, start_s=0.0, end_s=GAP_DUR_S, montage="0")
    _rendre_source(tmp_path, clip_id)
    clip = _clip_row(tmp_path, clip_id)
    assert clip["status"] == "done", f"rendu (montage=0) echoue : {clip['error']}"

    out = Path(clip["output_path"])
    info = probe_media(out)
    ecart = abs(info.duration_s - GAP_DUR_S)
    assert ecart <= 0.1, (
        f"{out}: duree mesuree {info.duration_s}s, attendue {GAP_DUR_S}s (source, montage=0, "
        f"aucune coupe), ecart {ecart:.3f}s")


# --------------------------------------------------------------------------
# Case 3 : un pic net donne un teaser -- lu sur sa propre entree seekee, son a
# 500 Hz sur sa duree, frame a 0.3 s non noire.
# --------------------------------------------------------------------------

TEASER_DUR_S = 9.0
TEASER_BURST = (4.5, 5.5, 500.0, 0.7)  # (debut,fin,freq,amplitude) -- seul son du clip
TEASER_WORD = (4.55, 5.55, "wow")


@pytest.fixture(scope="module")
def teaser_source(tmp_path_factory) -> Path:
    d = tmp_path_factory.mktemp("teaser_src")
    audio = _write_tone_wav(d / "teaser_tone.wav", duration_s=TEASER_DUR_S,
                            segments=[TEASER_BURST])
    return _mux_video_with_audio(d / "teaser_source.mp4", audio, duration_s=TEASER_DUR_S)


def test_clip_with_a_proven_peak_gets_a_teaser(tmp_path, teaser_source, monkeypatch):
    """Case 3 : un clip silencieux sauf un pic net (+70 dB de prominence, bien
    au-dessus du seuil de 6 dB) doit produire un teaser -- [vc0] lu sur une
    entree dediee seekee sur [teaser.src_start, teaser.src_end] (revue finale
    V6 : lu sur [0:v], il faisait bufferiser tout le corps en RAM), le son de
    sortie sur la duree du teaser est la bouffee a 500 Hz (seul son du clip),
    et la frame extraite a 0.3s de la sortie n'est pas noire (un teaser
    tronque a rien rendrait du noir)."""
    appels: list[tuple[dict, list[str]]] = []
    reel_build_render_cmd = queue_mod.build_render_cmd

    def _espion(*args, **kwargs):
        cmd = reel_build_render_cmd(*args, **kwargs)
        appels.append((kwargs, cmd))
        return cmd

    monkeypatch.setattr(queue_mod, "build_render_cmd", _espion)

    db_path = tmp_path / "clipforge.db"
    words = [TEASER_WORD]
    clip_id = _seed_source_clip(db_path, source=teaser_source, width=1920, height=1080,
                                fps=30.0, words=words, start_s=0.0, end_s=TEASER_DUR_S,
                                montage="1")
    _rendre_source(tmp_path, clip_id)
    clip = _clip_row(tmp_path, clip_id)
    assert clip["status"] == "done", f"rendu du clip teaser echoue : {clip['error']}"
    assert appels, "build_render_cmd n'a jamais ete appele"

    # Teaser attendu, recompute avec la MEME primitive (choose_cold_open) sur
    # l'audio REELLEMENT extrait par ce rendu.
    wav = tmp_path / "jobs" / f"clip{clip_id}" / montage_plan_mod.CLIP_AUDIO_NAME
    samples, sr = read_wav_mono(wav)
    energy = fine_energy(samples, sr)
    mots_rel = [Word(start=s, end=e, text=t) for s, e, t in words]
    teaser = choose_cold_open(mots_rel, energy, clip_dur_s=TEASER_DUR_S)
    assert teaser is not None, (
        "le clip de test doit produire un teaser (pic net attendu) -- "
        f"energie mesuree : {energy[:5]}...")

    kwargs, cmd = appels[-1]
    video_cut_graph = kwargs["video_cut"]
    m = _TEASER_INPUT_RE.search(video_cut_graph)
    assert m, f"aucun [vc0] lu sur une entree dediee dans : {video_cut_graph}"
    entrees = [i for i, arg in enumerate(cmd) if arg == "-i"]
    k = int(m.group(1))
    assert 0 < k < len(entrees), f"[vc0] lit l'entree {k}, la commande en a {len(entrees)} : {cmd}"
    args = cmd[entrees[k - 1] + 2: entrees[k] + 2]
    debut = float(args[args.index("-ss") + 1])
    fin = debut + float(args[args.index("-t") + 1])
    assert float(m.group(2)) == float(args[args.index("-t") + 1]), (
        f"video du teaser bornee a {m.group(2)}, entree seekee sur -t {args}")
    assert args[-1] == str(teaser_source), f"entree {k} = {args}, attendu la source"
    recul = min(1.0, teaser.src_start)
    assert abs(debut - (teaser.src_start - recul)) < 0.01 and abs(fin - teaser.src_end) < 0.01, (
        f"entree teaser seekee sur [{debut},{fin}], attendu le teaser "
        f"[{teaser.src_start},{teaser.src_end}] avec {recul} s de recul -- arguments {args}")

    out = Path(clip["output_path"])
    burst_debut, burst_fin, burst_hz, _ = TEASER_BURST
    src_lo = max(teaser.src_start, burst_debut) + 0.1
    src_hi = min(teaser.src_end, burst_fin) - 0.1
    assert src_hi - src_lo > 0.3, f"teaser {teaser} trop loin de la bouffee {TEASER_BURST}"
    fenetre = _extract_audio_window(out, tmp_path / "fenetre_teaser.wav",
                                    start_s=src_lo - teaser.src_start,
                                    duration_s=src_hi - src_lo)
    mesuree = _dominant_frequency_hz(*read_wav_mono(fenetre))
    assert abs(mesuree - burst_hz) <= 20.0, (
        f"{out}: sortie [{src_lo - teaser.src_start:.3f},{src_hi - teaser.src_start:.3f}]s "
        f"(teaser {teaser}) attendait {burst_hz} Hz, mesure {mesuree:.1f} Hz")

    non_noire, luminance = _frame_not_black(out, at_s=0.3)
    assert non_noire, (
        f"{out}@0.3s : luminance moyenne {luminance:.2f}, attendue non noire -- un teaser "
        f"tronque a rien rendrait une frame quasi noire (teaser attendu "
        f"[{teaser.src_start},{teaser.src_end}])")


# --------------------------------------------------------------------------
# Case 4 : l'expression zoompan V6 (rampes smoothstep) est acceptee par
# ffmpeg -- LE cas qu'un test pur ne peut pas voir (une faute de syntaxe dans
# l'expression ne se revele qu'au parsing ffmpeg).
# --------------------------------------------------------------------------


def test_v6_eased_zoom_expression_with_ramps_is_accepted_by_ffmpeg(tmp_path, monkeypatch):
    """Aucun mot (words_json="[]") : pas de coupe, pas d'extraction audio reelle
    -- seule la fenetre de punch-in (issue de `signals`, independante des
    coupes) alimente le mouvement organique. zoom_expr contient alors les
    rampes smoothstep V6 (`clip(...)`), jamais testees par un test unitaire
    puisque ce sont justement des CHAINES ffmpeg jamais executees ailleurs."""
    appels: list[dict] = []
    reel_build_render_cmd = queue_mod.build_render_cmd

    def _espion(*args, **kwargs):
        appels.append(kwargs)
        return reel_build_render_cmd(*args, **kwargs)

    monkeypatch.setattr(queue_mod, "build_render_cmd", _espion)

    audio = _write_tone_wav(tmp_path / "zoom.wav", duration_s=20.0,
                            segments=[(0.0, 20.0, 440.0, 0.3)])
    source = _mux_video_with_audio(tmp_path / "zoom.mp4", audio, duration_s=20.0)
    clip_id = _seed(tmp_path / "clipforge.db", words_json="[]", montage="1",
                    hook=None, emoji=None, source=source)
    _rendre(tmp_path, clip_id)
    clip = _clip(tmp_path, clip_id)
    assert clip["status"] == "done", f"rendu (zoom V6) echoue : {clip['error']}"
    assert appels, "build_render_cmd n'a jamais ete appele"

    zoom_expr = appels[-1]["zoom_expr"]
    assert "clip(" in zoom_expr, (
        f"zoom_expr ne contient pas de rampe smoothstep V6 (clip(...)) -- "
        f"expression obtenue : {zoom_expr!r} (le test est vide si aucune fenetre "
        f"de punch-in n'a ete detectee)")

    out = Path(clip["output_path"])
    info = probe_media(out)
    assert info.duration_s > 0, f"{out}: duree mesuree {info.duration_s}s -- rendu invalide"


# --------------------------------------------------------------------------
# Case 6 (revue A2, integration M1) : 200 segments. Chaque segment coupe
# allonge le graphe d'environ 280 caracteres ; en ligne, la commande
# depassait la limite de CreateProcess (32 767) vers 105 segments, et
# l'expression de zoom a plat faisait echouer zoompan des 97 coupes.
# --------------------------------------------------------------------------

LONG_WORDS = [(round(0.2 + 0.96 * k, 3), round(0.6 + 0.96 * k, 3), f"m{k}") for k in range(200)]
LONG_DUR_S = round(LONG_WORDS[-1][1] + 0.3, 3)
LONG_FPS = 5


def test_a_200_segment_clip_renders_with_its_graph_in_a_file(tmp_path, monkeypatch):
    """200 mots de 0,4 s separes de 0,56 s de silence (trous coupables, au-dela
    du seuil standard de 0,55 s) : 200 morceaux. Le rendu aboutit, la commande
    tient sous la limite et lit son graphe dans le fichier du job ; la video
    dure ce que TimeMap annonce."""
    appels: list[tuple[dict, list[str]]] = []
    reel_build_render_cmd = queue_mod.build_render_cmd

    def _espion(*args, **kwargs):
        cmd = reel_build_render_cmd(*args, **kwargs)
        appels.append((kwargs, cmd))
        return cmd

    monkeypatch.setattr(queue_mod, "build_render_cmd", _espion)
    audio = _write_tone_wav(tmp_path / "long.wav", duration_s=LONG_DUR_S,
                            segments=[(s, e, 440.0, 0.3) for s, e, _ in LONG_WORDS])
    source = _mux_video_with_audio(tmp_path / "long.mp4", audio, duration_s=LONG_DUR_S,
                                   fps=LONG_FPS)
    clip_id = _seed_source_clip(tmp_path / "clipforge.db", source=source, width=1920,
                                height=1080, fps=float(LONG_FPS), words=LONG_WORDS, start_s=0.0,
                                end_s=LONG_DUR_S, montage="1")
    _rendre_source(tmp_path, clip_id)
    clip = _clip_row(tmp_path, clip_id)
    assert clip["status"] == "done", f"rendu a 200 segments echoue : {clip['error']}"

    kwargs, cmd = appels[-1]
    assert kwargs["video_cut"].count("trim=end_frame=") >= 200, "le scenario ne mord pas"
    longueur = len(subprocess.list2cmdline(cmd))
    assert "-/filter_complex" in cmd and longueur < 32767, (longueur, cmd[:12])

    wav = tmp_path / "jobs" / f"clip{clip_id}" / montage_plan_mod.CLIP_AUDIO_NAME
    samples, sr = read_wav_mono(wav)
    mots_rel = [Word(start=s, end=e, text=t) for s, e, t in LONG_WORDS]
    edl = build_edl(mots_rel, fine_energy(samples, sr), clip_dur_s=LONG_DUR_S,
                    cut_style="standard")
    duree = TimeMap.from_edl(edl).output_duration
    info = probe_media(Path(clip["output_path"]))
    assert abs(info.duration_s - duree) <= 0.25, (info.duration_s, duree)
