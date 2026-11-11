import subprocess
from pathlib import Path

import pytest
import yt_dlp

from clipforge.pipeline import download as dl
from clipforge.pipeline import remux
from clipforge.pipeline.download import (
    DownloadError,
    download_vod,
    hook_progress_pct,
    twitch_vod_id,
)
from clipforge.pipeline.probe import MediaInfo, ProbeError, probe_media

FIXTURE = Path(__file__).parent / "fixtures" / "fixture.mp4"


def test_twitch_vod_id():
    assert twitch_vod_id("https://www.twitch.tv/videos/612942303") == "612942303"
    assert twitch_vod_id("https://twitch.tv/videos/1?t=1h2m") == "1"
    assert twitch_vod_id("https://youtube.com/watch?v=x") is None
    assert twitch_vod_id("https://eviltwitch.tv/videos/123") is None
    assert twitch_vod_id("https://evil.com/?x=.twitch.tv/videos/123") is None


def test_hook_progress_pct_bytes():
    assert hook_progress_pct({"total_bytes": 200, "downloaded_bytes": 50}) == 25.0
    assert hook_progress_pct({"total_bytes_estimate": 100, "downloaded_bytes": 150}) == 100.0


def test_hook_progress_pct_fragments_fallback():
    assert hook_progress_pct({"fragment_index": 30, "fragment_count": 120}) == 25.0
    assert hook_progress_pct({"downloaded_bytes": 999}) is None
    assert hook_progress_pct({"fragment_index": 3, "fragment_count": None}) is None


def _faux_ydl(monkeypatch, fichier: Path, *, duree: float = 10.0,
              deja_present: bool = False) -> dict:
    """Remplace yt_dlp.YoutubeDL par un double qui ne pose que `fichier`.

    deja_present rejoue la REPRISE mesuree le 2026-08-30 : yt-dlp trouve le
    fichier, annonce « already downloaded », n'appelle aucun hook 'finished' et
    saute son post-traitement. C'est ce silence qui renvoyait download_vod sur
    prepare_filename, donc sur un succes declare sans le moindre controle.
    """
    vus: dict = {}

    class FauxYDL:
        def __init__(self, opts):
            vus["opts"] = opts
            self._hooks = opts.get("progress_hooks", [])

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def extract_info(self, url, download=True):
            vus["url"] = url
            if not deja_present:
                for h in self._hooks:
                    h({"status": "finished", "filename": str(fichier)})
            return {"id": fichier.stem, "title": "Un stream", "duration": duree}

        def prepare_filename(self, info):
            return str(fichier)

    monkeypatch.setattr(yt_dlp, "YoutubeDL", FauxYDL)
    return vus


def _sans_ffmpeg(monkeypatch, *, duree_mesuree: float | None = 10.0,
                 remux_leve: Exception | None = None) -> list:
    """Neutralise ffmpeg et ffprobe : ces tests jugent la LOGIQUE de validation.

    Retourne la liste des appels vus, dans l'ordre : la reparation doit toujours
    passer AVANT la mesure, sinon la mesure porte sur le conteneur casse.
    """
    appels: list = []

    def faux_ensure(path, **kw):
        appels.append(("remux", Path(path), kw))
        if remux_leve:
            raise remux_leve
        return True

    def faux_probe(path):
        appels.append(("probe", Path(path), {}))
        if duree_mesuree is None:
            raise ProbeError("Duree indisponible")
        return MediaInfo(duration_s=duree_mesuree, width=1920, height=1080, fps=30.0)

    monkeypatch.setattr(dl.remux, "ensure_seekable", faux_ensure)
    monkeypatch.setattr(dl, "probe_media", faux_probe)
    return appels


def _poser_fichier(tmp_path: Path) -> Path:
    f = tmp_path / "v2836283171.mp4"
    f.write_bytes(b"x" * 4096)
    return f


def test_download_hands_the_repair_over_to_clipforge(tmp_path, monkeypatch):
    """D2 : la passe FixupM3u8 de yt-dlp (`ffmpeg -map 0`) embarque les flux de
    donnees des coupures pub et ne converge pas. On la coupe, et on coupe avec
    elle les 1,9 Mo de lignes de progression mesurees sur une VOD de 4 h 46."""
    vus = _faux_ydl(monkeypatch, _poser_fichier(tmp_path))
    _sans_ffmpeg(monkeypatch)
    download_vod("https://twitch.tv/videos/2836283171", tmp_path)
    assert vus["opts"]["fixup"] == "never"
    assert vus["opts"]["noprogress"] is True
    assert vus["opts"]["quiet"] is True


def test_download_repairs_before_measuring_and_returns_the_measured_duration(
        tmp_path, monkeypatch):
    fichier = _poser_fichier(tmp_path)
    _faux_ydl(monkeypatch, fichier, duree=17170.0)
    appels = _sans_ffmpeg(monkeypatch, duree_mesuree=17165.0)

    def suivi_reparation(pct: float) -> None:
        pass

    path, titre, duree = download_vod(
        "https://twitch.tv/videos/2836283171", tmp_path,
        progress_cb=lambda p: None, repair_cb=suivi_reparation)
    assert (path, titre) == (fichier, "Un stream")
    assert duree == 17165.0                              # la mesure fait foi, pas l'annonce
    assert [a[0] for a in appels] == ["remux", "probe"]
    assert appels[0][2]["duration_s"] == 17170.0
    # La reparation a son PROPRE suivi : lui donner celui du telechargement ferait
    # extrapoler un temps restant depuis le debut du telechargement.
    assert appels[0][2]["progress_cb"] is suivi_reparation


def test_download_refuses_a_file_it_cannot_read(tmp_path, monkeypatch):
    """D1, coeur du defaut : un fichier PRESENT ne vaut pas un fichier exploitable."""
    _faux_ydl(monkeypatch, _poser_fichier(tmp_path))
    _sans_ffmpeg(monkeypatch, duree_mesuree=None)
    with pytest.raises(DownloadError) as err:
        download_vod("https://twitch.tv/videos/2836283171", tmp_path)
    message = str(err.value).lower()
    assert "incomplet" in message
    assert "relance" in message


def test_download_refuses_a_file_the_repair_could_not_heal(tmp_path, monkeypatch):
    _faux_ydl(monkeypatch, _poser_fichier(tmp_path))
    _sans_ffmpeg(monkeypatch, remux_leve=remux.RemuxError("interrompu apres 30 min"))
    with pytest.raises(DownloadError, match="incomplet"):
        download_vod("https://twitch.tv/videos/2836283171", tmp_path)


def test_a_zero_duration_is_never_a_success(tmp_path, monkeypatch):
    _faux_ydl(monkeypatch, _poser_fichier(tmp_path))
    _sans_ffmpeg(monkeypatch, duree_mesuree=0.0)
    with pytest.raises(DownloadError, match="incomplet"):
        download_vod("https://twitch.tv/videos/2836283171", tmp_path)


def test_a_missing_file_is_still_reported(tmp_path, monkeypatch):
    """Le controle le plus ancien reste : rien a valider si rien n'a ete ecrit."""
    _faux_ydl(monkeypatch, tmp_path / "jamais_ecrit.mp4")
    _sans_ffmpeg(monkeypatch)
    with pytest.raises(DownloadError, match="introuvable"):
        download_vod("https://twitch.tv/videos/2836283171", tmp_path)


@pytest.mark.integration
def test_a_leftover_raw_ts_is_healed_never_blessed(tmp_path, monkeypatch):
    """LE scenario de reprise mesure le 2026-08-30, de bout en bout.

    Le fichier est deja la, en MPEG-TS brut sous un nom .mp4 ; yt-dlp annonce
    « already downloaded » et ne repare rien. Avant, download_vod declarait un
    succes sur ce flux brut et le sondage mourait six secondes plus tard, a
    chaque reprise, indefiniment. Il doit maintenant en sortir un MP4 lisible.
    """
    reste = tmp_path / "v2836283171.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", str(FIXTURE), "-t", "10", "-c", "copy",
         "-f", "mpegts", str(reste)], check=True)
    _faux_ydl(monkeypatch, reste, duree=10.0, deja_present=True)
    assert remux.is_mpegts(reste) is True

    path, _, duree = download_vod("https://twitch.tv/videos/2836283171", tmp_path)

    assert path == reste
    assert remux.is_mpegts(path) is False
    assert 9.0 <= duree <= 11.0
    assert probe_media(path).duration_s == duree
    assert list(tmp_path.glob("*remux.tmp*")) == []


def test_setting_a_source_aside_never_overwrites_an_earlier_one(tmp_path):
    """Deux telechargements qui echouent au meme endroit ne doivent pas s'effacer.

    Le fichier est RENOMME, jamais supprime : il pese des gigaoctets et porte
    peut-etre la seule copie d'un live. C'est a l'utilisateur de trancher.
    """
    a = tmp_path / "v1.mp4"
    a.write_bytes(b"premier")
    assert dl.mettre_de_cote(a).name == "v1.corrompu.mp4"
    b = tmp_path / "v1.mp4"
    b.write_bytes(b"second")
    assert dl.mettre_de_cote(b).name == "v1.corrompu (2).mp4"
    assert (tmp_path / "v1.corrompu.mp4").read_bytes() == b"premier"
    assert (tmp_path / "v1.corrompu (2).mp4").read_bytes() == b"second"
    assert not (tmp_path / "v1.mp4").exists()
