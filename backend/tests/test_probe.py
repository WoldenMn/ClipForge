import json
import subprocess
from pathlib import Path

import pytest

from clipforge.pipeline import probe as probe_mod
from clipforge.pipeline.probe import (
    ProbeError,
    ProbeUnavailable,
    _parse_probe_json,
    probe_media,
)

FIXTURE = Path(__file__).parent / "fixtures" / "fixture.mp4"


@pytest.mark.integration
def test_probe_fixture():
    info = probe_media(FIXTURE)
    assert info.width == 1920 and info.height == 1080
    assert 29.5 <= info.duration_s <= 30.5
    assert 29 <= info.fps <= 31


def test_parse_fps_zero_denominator(caplog):
    """Revue A2, fix round 1 (M2) : le repli a 30 ips se faisait sans trace ;
    en V6 toute la video du clip est reechantillonnee sur cette cadence."""
    data = {
        "format": {"duration": "30.0"},
        "streams": [{"codec_type": "video", "width": 1920, "height": 1080,
                     "avg_frame_rate": "0/0"}],
    }
    with caplog.at_level("WARNING"):
        info = _parse_probe_json(data)
    assert info.fps == 30.0
    assert "0/0" in caplog.text and "30" in caplog.text, caplog.text


def test_parse_fps_ntsc():
    data = {
        "format": {"duration": "30.0"},
        "streams": [{"codec_type": "video", "width": 1920, "height": 1080,
                     "avg_frame_rate": "60000/1001"}],
    }
    info = _parse_probe_json(data)
    assert info.fps == pytest.approx(59.94, abs=0.01)


def test_parse_no_video_stream():
    data = {
        "format": {"duration": "30.0"},
        "streams": [{"codec_type": "audio"}],
    }
    with pytest.raises(ProbeError):
        _parse_probe_json(data)


def test_parse_no_duration_blames_the_container_not_a_capture_crash():
    """Le message accusait un « crash OBS » sur une VOD Twitch telechargee par
    ClipForge lui-meme, ou aucun OBS n'intervient (test reel du 2026-08-30). Ce
    qui est en cause est le conteneur, et le remede est de relancer l'analyse."""
    data = {
        "format": {},
        "streams": [{"codec_type": "video", "width": 1920, "height": 1080,
                     "avg_frame_rate": "30/1"}],
    }
    with pytest.raises(ProbeError) as err:
        _parse_probe_json(data)
    message = str(err.value)
    assert "OBS" not in message
    assert "conteneur" in message.lower()
    assert "relance" in message.lower()


def test_parse_lit_la_duree_du_flux_video_a_part_du_conteneur():
    """Revue finale V6 (lot C, C3) : un rendu dont un morceau tombe dans un trou
    video garde un conteneur et un audio entiers, seule la video raccourcit."""
    data = {
        "format": {"duration": "9.600000"},
        "streams": [{"codec_type": "video", "width": 1080, "height": 1920,
                     "avg_frame_rate": "60/1", "duration": "9.000000"},
                    {"codec_type": "audio", "duration": "9.600000"}],
    }
    info = _parse_probe_json(data)
    assert info.duration_s == 9.6 and info.video_duration_s == 9.0
    del data["streams"][0]["duration"]
    assert _parse_probe_json(data).video_duration_s is None


def _faux_ffprobe(monkeypatch, *, leve=None, code=0, stdout=""):
    vu = {}

    def faux_run(cmd, **kw):
        vu["cmd"], vu["kw"] = cmd, kw
        if leve is not None:
            raise leve
        return subprocess.CompletedProcess(cmd, code, stdout=stdout, stderr="boum")

    monkeypatch.setattr("clipforge.pipeline.probe.subprocess.run", faux_run)
    return vu


def test_la_sonde_est_bornee_dans_le_temps(monkeypatch, tmp_path):
    """Une sonde sans borne bloque le rendu pour toujours (chemin reseau coupe,
    index a reconstruire sur plusieurs Go)."""
    media = tmp_path / "m.mp4"
    media.write_bytes(b"x")
    vu = _faux_ffprobe(monkeypatch, code=0, stdout=json.dumps(
        {"format": {"duration": "10.0"},
         "streams": [{"codec_type": "video", "width": 1, "height": 1,
                      "avg_frame_rate": "30/1"}]}))
    probe_media(media)
    assert vu["kw"]["timeout"] == probe_mod.PROBE_TIMEOUT_S


def test_une_sonde_qui_ne_rend_pas_la_main_est_une_panne_de_sonde(monkeypatch, tmp_path):
    media = tmp_path / "m.mp4"
    media.write_bytes(b"x")
    _faux_ffprobe(monkeypatch, leve=subprocess.TimeoutExpired(cmd="ffprobe", timeout=60))
    with pytest.raises(ProbeUnavailable, match="60"):
        probe_media(media)


def test_une_sortie_de_sonde_illisible_est_une_panne_de_sonde(monkeypatch, tmp_path):
    """json.JSONDecodeError remontait telle quelle : ni ProbeError, ni message
    qui dise que c'est la sonde qui a rendu n'importe quoi."""
    media = tmp_path / "m.mp4"
    media.write_bytes(b"x")
    _faux_ffprobe(monkeypatch, code=0, stdout="<html>proxy d'entreprise</html>")
    with pytest.raises(ProbeUnavailable, match="illisible"):
        probe_media(media)


def test_une_sonde_absente_est_une_panne_de_sonde(monkeypatch, tmp_path):
    media = tmp_path / "m.mp4"
    media.write_bytes(b"x")
    _faux_ffprobe(monkeypatch, leve=FileNotFoundError("ffprobe"))
    with pytest.raises(ProbeUnavailable, match="ffprobe"):
        probe_media(media)


def test_un_media_illisible_reste_une_erreur_de_media_pas_de_sonde(monkeypatch, tmp_path):
    """ffprobe qui repond rc != 0 accuse le FICHIER, pas lui-meme : le rendu a
    droit a sa seconde tentative."""
    media = tmp_path / "m.mp4"
    media.write_bytes(b"x")
    _faux_ffprobe(monkeypatch, code=1)
    with pytest.raises(ProbeError) as err:
        probe_media(media)
    assert not isinstance(err.value, ProbeUnavailable)


_VIDEO_SANS_TAILLE = '{"streams": [{"codec_type": "video"}], "format": {"duration": "10"}}'


@pytest.mark.parametrize("stdout", ["{}", "[]", '{"streams": "x"}', _VIDEO_SANS_TAILLE])
def test_une_sortie_de_sonde_inattendue_est_une_panne_de_sonde(monkeypatch, tmp_path, stdout):
    """Fix round 2 (K4) : une sonde qui rend {} levait KeyError('streams') brut,
    que personne ne rattrapait (mesure : rendu mort avant la moindre tentative,
    erreur en base « 'streams' »)."""
    media = tmp_path / "m.mp4"
    media.write_bytes(b"x")
    _faux_ffprobe(monkeypatch, code=0, stdout=stdout)
    with pytest.raises(ProbeUnavailable, match="inattendue"):
        probe_media(media)


def test_la_fin_d_un_flux_est_son_debut_plus_sa_duree():
    """Fix round 2 (K4) : audio de 2,98 a 30 s, lu comme finissant a 27,02 s."""
    data = {
        "format": {"duration": "30.0"},
        "streams": [{"codec_type": "video", "width": 1920, "height": 1080,
                     "avg_frame_rate": "30/1", "start_time": "0.000000",
                     "duration": "30.000000"},
                    {"codec_type": "audio", "start_time": "2.978667",
                     "duration": "27.021333"}],
    }
    info = _parse_probe_json(data)
    assert info.audio_start_s == pytest.approx(2.978667)
    assert info.audio_end_s == pytest.approx(30.0) and info.video_end_s == pytest.approx(30.0)
    del data["streams"][1]["start_time"]
    assert _parse_probe_json(data).audio_end_s == pytest.approx(27.021333)
    data["streams"][1]["start_time"] = "N/A"
    assert _parse_probe_json(data).audio_start_s == 0.0


# ffprobe de decale_video_courte.mp4, source du relecteur (revue finale V6,
# fix round 3, F2) : conteneur qui demarre a 1,479 s, video de 1,5 a 28,0 s.
DECALE_VIDEO_COURTE = {
    "format": {"start_time": "1.478667", "duration": "30.021333"},
    "streams": [{"codec_type": "video", "width": 1920, "height": 1080,
                 "avg_frame_rate": "30/1", "start_time": "1.500000", "duration": "26.500000"},
                {"codec_type": "audio", "start_time": "1.478667", "duration": "30.021333"}],
}


def test_debuts_et_fins_se_comptent_depuis_le_debut_du_fichier():
    """-ss et format.duration comptent depuis format.start_time : la video de
    cette source finit a 26,521 s du debut du fichier, pas a 28,0."""
    info = _parse_probe_json(DECALE_VIDEO_COURTE)
    assert info.video_start_s == pytest.approx(0.021333)
    assert info.video_end_s == pytest.approx(26.521333)
    assert info.audio_start_s == pytest.approx(0.0)
    assert info.audio_end_s == pytest.approx(30.021333)


@pytest.mark.integration
def test_un_mkv_reel_a_une_piste_audio_sans_duree_de_flux(tmp_path):
    """La premisse du fix round 3 (F3), mesuree sur un vrai MKV : ffprobe ne
    donne aucune duree par flux, seule la presence des flux est lisible."""
    src = tmp_path / "obs.mkv"
    proc = subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=30:duration=2",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-c:v", "libx264",
         "-preset", "veryfast", "-pix_fmt", "yuv420p", "-c:a", "aac", str(src)],
        capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stderr[-800:]
    info = probe_media(src)
    assert info.audio_duration_s is None and info.video_duration_s is None, info
    assert info.audio_present is True and info.duration_s == pytest.approx(2.0, abs=0.1), info


def _sonde(origine: str | None, audio_start: str | None) -> dict:
    flux = [{"codec_type": "video", "width": 1920, "height": 1080,
             "avg_frame_rate": "30/1"}]
    if audio_start is not None:
        flux.append({"codec_type": "audio", "start_time": audio_start})
    conteneur = {"duration": "30.0"}
    if origine is not None:
        conteneur["start_time"] = origine
    return {"format": conteneur, "streams": flux}


def test_audio_start_se_compte_depuis_le_debut_du_fichier():
    """Un MPEG-TS dont le conteneur ET l'audio demarrent a 1,4 s a un audio a
    l'heure : c'est l'ecart entre les deux qui decale le WAV extrait, pas le PTS
    absolu. Mesure ffmpeg 9.0.1 : le WAV est decale d'exactement cet ecart."""
    assert _parse_probe_json(_sonde("1.4", "1.4")).audio_start_s == 0.0


def test_audio_start_retient_le_retard_reel_de_la_piste():
    info = _parse_probe_json(_sonde("0.0", "2.958667"))
    assert info.audio_start_s == pytest.approx(2.958667)


def test_audio_start_vaut_zero_sans_piste_audio_ni_start_time():
    assert _parse_probe_json(_sonde(None, None)).audio_start_s == 0.0
    assert _parse_probe_json(_sonde("N/A", "N/A")).audio_start_s == 0.0


@pytest.mark.parametrize("sortie", ["", "pas du json", '{"streams": []}', "{}"])
def test_sortie_illisible_sort_en_probe_error(monkeypatch, tmp_path, sortie):
    """Les appelants annoncent tous ProbeError : une JSONDecodeError ou un
    KeyError nus leur passaient entre les doigts et tuaient le rendu."""
    source = tmp_path / "s.mp4"; source.write_bytes(b"x")

    class P: returncode = 0; stdout = sortie; stderr = ""

    monkeypatch.setattr("clipforge.pipeline.probe.subprocess.run",
                        lambda *a, **k: P())
    with pytest.raises(ProbeError):
        probe_media(source)
