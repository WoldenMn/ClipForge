import subprocess
import wave
from pathlib import Path

import pytest
from test_probe import DECALE_VIDEO_COURTE

from clipforge import db as dbmod
from clipforge.pipeline.audio_extract import AudioExtractError, extract_wav
from clipforge.pipeline.probe import MediaInfo, ProbeError, _parse_probe_json
from clipforge.pipeline.steps import build_real_steps

FIXTURE = Path(__file__).parent / "fixtures" / "fixture.mp4"


def _ecrire_wav(path: Path, secondes: float, sr: int = 16000) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(bytes(2 * round(secondes * sr)))


def _faux_ffmpeg(monkeypatch, ecrire, vue: dict | None = None):
    """subprocess.run d'extract_wav : `ecrire(chemin_part)` pose la sortie, rc=0."""
    vue = {} if vue is None else vue

    def faux_run(cmd, **kw):
        vue["cmd"], vue["kw"] = cmd, kw
        ecrire(Path(cmd[-1]))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr("clipforge.pipeline.audio_extract.subprocess.run", faux_run)
    return vue


@pytest.fixture
def sonde(monkeypatch):
    """Pose ce que ffprobe repondra, sans lancer de sous-processus.

    `retard=None` simule une sonde en panne. Sans cette fixture, chaque test
    unitaire de commande ferait tourner un vrai ffprobe sur un fichier bidon.
    """
    def poser(retard: float | None = 0.0):
        def reponse(_source):
            if retard is None:
                raise ProbeError("sonde indisponible")
            return MediaInfo(duration_s=30.0, width=1920, height=1080, fps=30.0,
                             audio_start_s=retard)
        monkeypatch.setattr("clipforge.pipeline.audio_extract.probe_media", reponse)
    return poser


@pytest.fixture
def commande(monkeypatch):
    """Capture la commande ffmpeg sans l'executer.

    Le WAV pose fait la duree demandee par `-t` : extract_wav relit ce qu'il
    vient d'ecrire (revue finale V6, lot C), un fichier bidon serait refuse
    comme illisible avant que le test ait pu lire la commande.
    """
    vue: dict = {}

    def poser(part: Path) -> None:
        cmd = vue["cmd"]
        _ecrire_wav(part, float(cmd[cmd.index("-t") + 1]) if "-t" in cmd else 1.0)

    return _faux_ffmpeg(monkeypatch, poser, vue)


@pytest.mark.integration
def test_extract_wav(tmp_path):
    out = extract_wav(FIXTURE, tmp_path / "a.wav")
    with wave.open(str(out)) as w:
        assert w.getnchannels() == 1
        assert w.getframerate() == 16000
        assert w.getnframes() / 16000 > 29


@pytest.mark.integration
def test_extract_wav_source_missing(tmp_path):
    dest = tmp_path / "a.wav"
    with pytest.raises(AudioExtractError):
        extract_wav(tmp_path / "absente.mp4", dest)
    assert not dest.is_file()


def test_extract_wav_borne_ajoute_ss_et_t(sonde, commande, tmp_path):
    sonde()
    src = tmp_path / "s.mp4"; src.write_bytes(b"x")
    extract_wav(src, tmp_path / "out.wav", start_s=12.5, end_s=55.0)
    i = commande["cmd"].index("-ss")
    assert commande["cmd"][i + 1] == "12.5"
    assert commande["cmd"][commande["cmd"].index("-t") + 1] == "42.5"
    assert commande["cmd"].index("-ss") < commande["cmd"].index("-i")  # seek d'entree, rapide


def test_extract_wav_borne_start_seul_leve_erreur(tmp_path):
    src = tmp_path / "s.mp4"; src.write_bytes(b"x")
    with pytest.raises(ValueError):
        extract_wav(src, tmp_path / "out.wav", start_s=12.5)


def test_extract_wav_borne_end_seul_leve_erreur(tmp_path):
    src = tmp_path / "s.mp4"; src.write_bytes(b"x")
    with pytest.raises(ValueError):
        extract_wav(src, tmp_path / "out.wav", end_s=55.0)


def test_extract_wav_borne_end_lte_start_leve_erreur(tmp_path):
    src = tmp_path / "s.mp4"; src.write_bytes(b"x")
    with pytest.raises(ValueError):
        extract_wav(src, tmp_path / "out.wav", start_s=55.0, end_s=55.0)
    with pytest.raises(ValueError):
        extract_wav(src, tmp_path / "out.wav", start_s=55.0, end_s=12.5)


@pytest.fixture()
def source(tmp_path) -> Path:
    src = tmp_path / "s.mp4"
    src.write_bytes(b"x")
    return src


def _assert_rien_de_pose(tmp_path: Path) -> None:
    assert not (tmp_path / "out.wav").exists()
    assert not (tmp_path / "out.wav.part").exists()


def test_un_wav_sans_aucune_trame_est_un_echec(monkeypatch, tmp_path, source):
    """Revue finale V6 (lot C, C2) : un seek au-dela de la fin rend rc=0 et un
    WAV de 78 octets, 0 echantillon (sonde ffmpeg 9.0.1)."""
    _faux_ffmpeg(monkeypatch, lambda part: _ecrire_wav(part, 0.0))
    with pytest.raises(AudioExtractError, match="0 echantillon"):
        extract_wav(source, tmp_path / "out.wav", start_s=12.0, end_s=14.0)
    _assert_rien_de_pose(tmp_path)


def test_l_extraction_non_bornee_refuse_aussi_un_wav_vide(monkeypatch, tmp_path, source):
    _faux_ffmpeg(monkeypatch, lambda part: _ecrire_wav(part, 0.0))
    with pytest.raises(AudioExtractError, match="0 echantillon"):
        extract_wav(source, tmp_path / "out.wav")
    _assert_rien_de_pose(tmp_path)


def test_un_wav_tronque_est_un_echec_qui_dit_les_deux_durees(monkeypatch, tmp_path, source):
    """Piste audio plus courte que la video : 1 s rendue pour 6 demandees, rc=0."""
    _faux_ffmpeg(monkeypatch, lambda part: _ecrire_wav(part, 1.0))
    with pytest.raises(AudioExtractError, match="1.000 s.*6.0 s"):
        extract_wav(source, tmp_path / "out.wav", start_s=2.0, end_s=8.0)
    _assert_rien_de_pose(tmp_path)


def test_un_wav_sain_a_quelques_ms_pres_passe(monkeypatch, tmp_path, source):
    """Mesure (ffmpeg 9.0.1) : une extraction saine qui part du debut d'un flux
    AAC rend jusqu'a 13,4 ms de moins que demande."""
    _faux_ffmpeg(monkeypatch, lambda part: _ecrire_wav(part, 6.0 - 0.0134))
    out = extract_wav(source, tmp_path / "out.wav", start_s=0.001, end_s=6.001)
    assert out.is_file() and not (tmp_path / "out.wav.part").exists()


@pytest.mark.parametrize("contenu", [b"RIFF", b"mp4", b"pas un wav du tout, vraiment pas"])
def test_un_wav_illisible_est_un_echec(monkeypatch, tmp_path, source, contenu):
    _faux_ffmpeg(monkeypatch, lambda part: part.write_bytes(contenu))
    with pytest.raises(AudioExtractError, match="illisible"):
        extract_wav(source, tmp_path / "out.wav", start_s=0.0, end_s=6.0)
    _assert_rien_de_pose(tmp_path)


def test_une_extraction_qui_ne_rend_pas_la_main_est_un_echec(monkeypatch, tmp_path, source):
    vue = {}

    def faux_run(cmd, **kw):
        vue["kw"] = kw
        Path(cmd[-1]).write_bytes(b"RIFF partiel")
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=kw.get("timeout"))

    monkeypatch.setattr("clipforge.pipeline.audio_extract.subprocess.run", faux_run)
    with pytest.raises(AudioExtractError, match="120"):
        extract_wav(source, tmp_path / "out.wav", start_s=0.0, end_s=6.0, timeout_s=120)
    assert vue["kw"]["timeout"] == 120
    _assert_rien_de_pose(tmp_path)


def test_sans_borne_de_temps_l_extraction_garde_son_comportement(monkeypatch, tmp_path, source):
    vue = _faux_ffmpeg(monkeypatch, lambda part: _ecrire_wav(part, 1.0))
    extract_wav(source, tmp_path / "out.wav")
    assert vue["kw"].get("timeout") is None


def test_l_etape_d_extraction_de_la_vod_echoue_sur_un_wav_vide(
        monkeypatch, tmp_path, source, conn):
    """L'autre appelant, pipeline/steps.py : extraction non bornee de la VOD
    entiere, 0 trame -> l'etape leve, aucun audio.wav pose pour la suite."""
    from clipforge import config

    monkeypatch.setattr(config, "JOBS_DIR", tmp_path / "jobs")
    _faux_ffmpeg(monkeypatch, lambda part: _ecrire_wav(part, 0.0))
    vod_id = dbmod.create_vod(conn, title="t", source_path=str(source))
    step_extract = dict(build_real_steps())["extracting_audio"]
    with pytest.raises(AudioExtractError):
        step_extract(conn, vod_id, {})
    assert not (tmp_path / "jobs" / f"vod{vod_id}" / "audio.wav").exists()


def _sonde_audio(monkeypatch, fin_audio: float | None, *, panne: Exception | None = None,
                 debut_audio: float = 0.0):
    """probe_media vue par extract_wav : la source dit ou commence et ou finit
    son flux audio."""
    def faux_probe(path):
        if panne is not None:
            raise panne
        duree = None if fin_audio is None else fin_audio - debut_audio
        return MediaInfo(duration_s=10.0, width=1920, height=1080, fps=30.0,
                         audio_duration_s=duree, audio_start_s=debut_audio)

    monkeypatch.setattr("clipforge.pipeline.audio_extract.probe_media", faux_probe)


def test_une_fenetre_au_bord_du_flux_audio_n_est_pas_une_troncature(
        monkeypatch, tmp_path, source):
    """Revue finale V6 (fix round 1, R5) : la tolerance de 50 ms rejetait une
    extraction saine quand le flux audio finit avant le conteneur. Mesure sur
    source fabriquee (audio 9,92 s sous un conteneur de 10,0 s) : 5,920 s lues
    pour 6,0 s demandees, soit 80 ms d'avance, refusees."""
    _faux_ffmpeg(monkeypatch, lambda part: _ecrire_wav(part, 5.92))
    _sonde_audio(monkeypatch, 9.92)
    out = extract_wav(source, tmp_path / "out.wav", start_s=4.0, end_s=10.0)
    assert out.is_file() and not (tmp_path / "out.wav.part").exists()


def test_un_wav_bien_plus_court_que_ce_que_la_source_a_reste_un_echec(
        monkeypatch, tmp_path, source):
    """Le plafond ne pardonne que ce qui manque a la source : 1 s pour 5,92 s
    disponibles reste une extraction tronquee."""
    _faux_ffmpeg(monkeypatch, lambda part: _ecrire_wav(part, 1.0))
    _sonde_audio(monkeypatch, 9.92)
    with pytest.raises(AudioExtractError, match="1.000 s"):
        extract_wav(source, tmp_path / "out.wav", start_s=4.0, end_s=10.0)
    _assert_rien_de_pose(tmp_path)


def test_une_source_non_sondable_garde_la_fenetre_demandee_et_le_dit(
        monkeypatch, tmp_path, source, caplog):
    _faux_ffmpeg(monkeypatch, lambda part: _ecrire_wav(part, 5.92))
    _sonde_audio(monkeypatch, None, panne=ProbeError("ffprobe introuvable"))
    with caplog.at_level("WARNING"), pytest.raises(AudioExtractError, match="tronquee"):
        extract_wav(source, tmp_path / "out.wav", start_s=4.0, end_s=10.0)
    assert "ffprobe" in caplog.text, caplog.text
    _assert_rien_de_pose(tmp_path)


def test_une_source_sans_duree_de_flux_audio_garde_la_fenetre_demandee(
        monkeypatch, tmp_path, source):
    _faux_ffmpeg(monkeypatch, lambda part: _ecrire_wav(part, 1.0))
    _sonde_audio(monkeypatch, None)
    with pytest.raises(AudioExtractError, match="tronquee"):
        extract_wav(source, tmp_path / "out.wav", start_s=2.0, end_s=8.0)


def test_l_extraction_compte_le_debut_de_l_audio(monkeypatch, tmp_path, source):
    """Audio de 0,5 a 9,92 s : fenetre [4 ; 10], 5,92 s lues. Lu sur la seule
    duree (9,42 s), 5,42 s disponibles : la borne tenait par hasard ; lu comme
    debut + duree, 5,92 s, la vraie reponse."""
    _faux_ffmpeg(monkeypatch, lambda part: _ecrire_wav(part, 5.3))
    _sonde_audio(monkeypatch, 9.92, debut_audio=0.5)
    with pytest.raises(AudioExtractError, match="5.920 s disponibles"):
        extract_wav(source, tmp_path / "out.wav", start_s=4.0, end_s=10.0)


@pytest.mark.parametrize("lue, refus", [(4.037, None), (3.9, "4.021 s disponibles")])
def test_la_fin_de_l_audio_se_compte_depuis_le_debut_du_fichier(
        monkeypatch, tmp_path, source, lue, refus):
    """Fix round 3 (F2), decale_video_courte.mp4 : audio de 1,479 a 31,5 s sur
    un conteneur qui demarre a 1,479 s. Fenetre [26 ; 32] : 4,021 s d'audio
    apres 26 s ; 4,037 s lues (mesure) etaient refusees contre 5,5 s
    disponibles, le debut du conteneur compte deux fois."""
    _faux_ffmpeg(monkeypatch, lambda part: _ecrire_wav(part, lue))
    monkeypatch.setattr("clipforge.pipeline.audio_extract.probe_media",
                        lambda path: _parse_probe_json(DECALE_VIDEO_COURTE))
    if refus is None:
        assert extract_wav(source, tmp_path / "out.wav", start_s=26.0, end_s=32.0).is_file()
    else:
        with pytest.raises(AudioExtractError, match=refus):
            extract_wav(source, tmp_path / "out.wav", start_s=26.0, end_s=32.0)


def _filtre(cmd: list[str]) -> str | None:
    return cmd[cmd.index("-af") + 1] if "-af" in cmd else None


def test_extract_wav_comble_le_trou_de_tete_de_la_vod(sonde, commande, tmp_path):
    """Sans ce silence, tout ce qui suit lit la VOD avec 2,96 s d'avance."""
    sonde(2.958667)
    src = tmp_path / "s.mp4"; src.write_bytes(b"x")
    extract_wav(src, tmp_path / "out.wav")
    af = _filtre(commande["cmd"])
    assert af == "aresample=16000,adelay=47339S:all=1"  # 2,958667 s a 16 kHz
    # Le « S » compte dans la frequence du point du graphe : le resample d'abord.
    assert af.index("aresample") < af.index("adelay")


def test_extract_wav_ne_touche_pas_une_source_a_l_heure(sonde, commande, tmp_path):
    sonde(0.0)
    src = tmp_path / "s.mp4"; src.write_bytes(b"x")
    extract_wav(src, tmp_path / "out.wav")
    assert _filtre(commande["cmd"]) is None


def test_extract_wav_ignore_un_retard_sous_la_milliseconde(sonde, commande, tmp_path):
    """Un start_time de cet ordre tient de l'arrondi de timebase, pas d'un retard."""
    sonde(0.0004)
    src = tmp_path / "s.mp4"; src.write_bytes(b"x")
    extract_wav(src, tmp_path / "out.wav")
    assert _filtre(commande["cmd"]) is None


def test_extract_wav_borne_refuse_une_fenetre_ouverte_avant_l_audio(sonde, commande,
                                                                    tmp_path):
    """Le silence ajoute par filtre et `-t` se disputent la duree : mesure ffmpeg
    9.0.1, `-ss 0 -t 6` sur un audio qui part a 2,96 s rend 3,04 s de silence au
    lieu de 6,0 s avec le son a 4,0 s. Refuser vaut mieux que rendre ca.

    Meme regle que le fix round 2 (K4) de la V6, qui la tenait apres coup sur
    la duree relue : elle se decide ici, avant de lancer ffmpeg."""
    sonde(2.958667)
    src = tmp_path / "s.mp4"; src.write_bytes(b"x")
    with pytest.raises(AudioExtractError, match="decale"):
        extract_wav(src, tmp_path / "out.wav", start_s=0.0, end_s=6.0)
    _assert_rien_de_pose(tmp_path)
    assert "cmd" not in commande  # refuse avant de lancer quoi que ce soit


def test_extract_wav_borne_apres_le_debut_de_l_audio_reste_intacte(sonde, commande,
                                                                   tmp_path):
    """Rien a rattraper quand la fenetre s'ouvre apres le debut de la piste."""
    sonde(2.958667)
    src = tmp_path / "s.mp4"; src.write_bytes(b"x")
    extract_wav(src, tmp_path / "out.wav", start_s=5.0, end_s=11.0)
    assert _filtre(commande["cmd"]) is None


def test_extract_wav_extrait_quand_meme_si_la_sonde_est_en_panne(sonde, commande,
                                                                 tmp_path):
    """Une sonde muette ne doit pas bloquer le pipeline : on repart d'avant."""
    sonde(None)
    src = tmp_path / "s.mp4"; src.write_bytes(b"x")
    out = extract_wav(src, tmp_path / "out.wav")
    assert out.is_file()
    assert _filtre(commande["cmd"]) is None
