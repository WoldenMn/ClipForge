import json
import logging
import struct
import subprocess
import sys
from pathlib import Path

import pytest

from clipforge.pipeline import remux
from clipforge.pipeline import remux_repair as repair
from clipforge.pipeline.probe import probe_media
from clipforge.pipeline.remux import RemuxError, ensure_seekable, is_fragmented

FIXTURE = Path(__file__).parent / "fixtures" / "fixture.mp4"


def _atom(typ: bytes, payload: bytes = b"") -> bytes:
    return struct.pack(">I", 8 + len(payload)) + typ + payload


def _mp4(*atoms: bytes) -> bytes:
    return _atom(b"ftyp", b"isom") + b"".join(atoms)


def _ecrire(path: Path, data: bytes) -> Path:
    path.write_bytes(data)
    return path


def _ts_octets(paquets: int = 8) -> bytes:
    """Un MPEG-TS synthetique : l'octet de synchro puis 187 octets, en boucle."""
    paquet = bytes([remux.TS_SYNC]) + b"\x00" * (remux.TS_PACKET - 1)
    return paquet * paquets


def _ts_multiflux(dest: Path, duree: str = "10") -> Path:
    """Un vrai MPEG-TS a quatre flux, nomme .mp4 comme le livre yt-dlp.

    Les flux surnumeraires tiennent le role des flux de donnees (timed_id3) que
    les coupures publicitaires Twitch injectent en cours de VOD : c'est eux que
    le `-map 0` de yt-dlp embarquait, et le multiplexeur MP4 attendait ensuite
    des horodatages qui n'arrivaient jamais.
    """
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", str(FIXTURE), "-i", str(FIXTURE),
         "-t", duree, "-map", "0:v:0", "-map", "1:v:0", "-map", "0:a:0", "-map", "1:a:0",
         "-c", "copy", "-f", "mpegts", str(dest)], check=True)
    return dest


def _ts_tronque(dest: Path, garde: float = 0.25) -> Path:
    """Un vrai MPEG-TS coupe net en plein milieu, sur une frontiere de paquet.

    Reproduit la source du user (test reel du 2026-08-30) : le fichier passe la
    detection de conteneur, ffmpeg le copie sans broncher et rend la main, et
    c'est le controle de duree qui refuse la sortie — 1620 s livrees pour
    17170 s attendues. Couper sur un multiple de 188 octets laisse un dernier
    paquet entier : c'est le contenu qui manque, pas la structure.
    """
    plein = _ts_multiflux(dest.with_name("plein.ts"))
    octets = plein.read_bytes()
    plein.unlink()
    dest.write_bytes(octets[:int(len(octets) * garde) // remux.TS_PACKET * remux.TS_PACKET])
    return dest


def _flux(path: Path) -> dict[str, int]:
    """Nombre de pistes par type, lu par ffprobe.

    En JSON et non en CSV : sur un MPEG-TS, `-of csv` reimprime la liste une fois
    par programme et comptait donc chaque piste deux fois. L'instrument disait
    quatre videos sur un fichier qui en porte deux.
    """
    sortie = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(path)],
        capture_output=True, text=True, check=True).stdout
    compte: dict[str, int] = {}
    for flux in json.loads(sortie)["streams"]:
        compte[flux["codec_type"]] = compte.get(flux["codec_type"], 0) + 1
    return compte


def test_plain_mp4_is_not_fragmented(tmp_path):
    """moov sans mvex : le fichier porte ses tables d'index, il se seeke."""
    f = _ecrire(tmp_path / "plain.mp4", _mp4(_atom(b"moov", _atom(b"mvhd")), _atom(b"mdat")))
    assert is_fragmented(f) is False


def test_fragmented_without_index_is_detected(tmp_path):
    f = _ecrire(tmp_path / "frag.mp4",
                _mp4(_atom(b"moov", _atom(b"mvex", _atom(b"trex"))),
                     _atom(b"moof"), _atom(b"mdat")))
    assert is_fragmented(f) is True


def test_fragmented_with_sidx_is_left_alone(tmp_path):
    """Un fragmente indexe se seeke par requetes Range : rien a reecrire."""
    f = _ecrire(tmp_path / "dash.mp4",
                _mp4(_atom(b"moov", _atom(b"mvex")), _atom(b"sidx"), _atom(b"moof")))
    assert is_fragmented(f) is False


def test_non_mp4_is_never_touched(tmp_path):
    f = _ecrire(tmp_path / "video.mkv", b"\x1a\x45\xdf\xa3" + b"matroska" * 64)
    assert is_fragmented(f) is False


def test_truncated_header_is_not_fragmented(tmp_path):
    assert is_fragmented(_ecrire(tmp_path / "court.mp4", b"\x00\x00")) is False


def test_missing_file_is_reported_not_raised(tmp_path, caplog):
    with caplog.at_level(logging.WARNING):
        assert is_fragmented(tmp_path / "absent.mp4") is False
    assert "illisible" in caplog.text


def test_mpegts_is_recognised_by_its_sync_bytes(tmp_path):
    """Le HLS Twitch arrive en MPEG-TS sous un nom .mp4 : c'est l'octet de synchro
    tous les 188 octets qui le trahit, jamais l'extension."""
    f = _ecrire(tmp_path / "v123.mp4", _ts_octets())
    assert remux.is_mpegts(f) is True
    assert is_fragmented(f) is False        # aucun atome MP4 : l'ancien detecteur est aveugle


def test_a_real_mp4_is_never_taken_for_mpegts(tmp_path):
    f = _ecrire(tmp_path / "plain.mp4", _mp4(_atom(b"moov", _atom(b"mvhd")), _atom(b"mdat")))
    assert remux.is_mpegts(f) is False


def test_a_lone_sync_byte_is_not_enough(tmp_path):
    """0x47 en tete arrive par hasard ; la periodicite a 188 octets, non."""
    f = _ecrire(tmp_path / "hasard.mp4", bytes([remux.TS_SYNC]) + b"\x00" * 900)
    assert remux.is_mpegts(f) is False


def test_missing_file_is_not_mpegts_either(tmp_path):
    assert remux.is_mpegts(tmp_path / "absent.mp4") is False


def test_besoin_de_remux_names_the_defect(tmp_path):
    ts = _ecrire(tmp_path / "ts.mp4", _ts_octets())
    frag = _ecrire(tmp_path / "frag.mp4",
                   _mp4(_atom(b"moov", _atom(b"mvex")), _atom(b"moof"), _atom(b"mdat")))
    sain = _ecrire(tmp_path / "sain.mp4", _mp4(_atom(b"moov", _atom(b"mvhd")), _atom(b"mdat")))
    assert remux.besoin_de_remux(ts) == "mpegts"
    assert remux.besoin_de_remux(frag) == "fragmente"
    assert remux.besoin_de_remux(sain) is None


def test_a_remux_that_never_converges_is_given_up(tmp_path):
    """D2 : la passe de reparation lancee par yt-dlp a relu 90 Go sans converger,
    et l'etape n'avait AUCUN delai maximum — 16 minutes d'interface figee sur
    « ~30 s restantes ». Un processus qui ne rend jamais la main doit etre tue,
    et l'echec dit."""
    interminable = [sys.executable, "-c", "import time; time.sleep(30)"]
    with pytest.raises(RemuxError, match="interrompu"):
        repair._run_ffmpeg(interminable, 0.0, None, timeout_s=0.5)


def test_ensure_seekable_refuses_without_disk_space(tmp_path, monkeypatch):
    f = _ecrire(tmp_path / "frag.mp4",
                _mp4(_atom(b"moov", _atom(b"mvex")), _atom(b"moof"), _atom(b"mdat")))
    monkeypatch.setattr(repair.shutil, "disk_usage",
                        lambda p: type("U", (), {"free": 0})())
    with pytest.raises(RemuxError, match="Espace disque"):
        ensure_seekable(f)
    assert list(tmp_path.glob("*.tmp.mp4")) == []


def test_failed_remux_keeps_the_source_and_leaves_no_debris(tmp_path):
    """Un ffmpeg en echec ne doit ni toucher la source ni laisser de .tmp."""
    data = _mp4(_atom(b"moov", _atom(b"mvex")), _atom(b"moof"), _atom(b"mdat"))
    f = _ecrire(tmp_path / "bidon.mp4", data)   # passe le detecteur, illisible par ffmpeg
    with pytest.raises(RemuxError):
        ensure_seekable(f)
    assert f.read_bytes() == data
    assert list(tmp_path.glob("*remux.tmp*")) == []


@pytest.mark.integration
def test_real_fixture_needs_no_remux(tmp_path):
    copie = Path(tmp_path / "fixture.mp4")
    copie.write_bytes(FIXTURE.read_bytes())
    avant = copie.stat().st_mtime_ns
    assert is_fragmented(copie) is False
    assert ensure_seekable(copie) is False
    assert copie.stat().st_mtime_ns == avant   # fichier sain : jamais reecrit


@pytest.mark.integration
def test_fragmented_file_becomes_seekable(tmp_path):
    frag = tmp_path / "frag.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", str(FIXTURE), "-t", "5", "-c", "copy",
         "-movflags", "frag_keyframe+empty_moov", str(frag)], check=True)
    assert is_fragmented(frag) is True
    avancement: list[float] = []
    assert ensure_seekable(frag, duration_s=5.0, progress_cb=avancement.append) is True
    assert is_fragmented(frag) is False
    assert frag.stat().st_size > 0
    assert avancement and avancement[-1] <= 100.0
    assert list(tmp_path.glob("*remux.tmp*")) == []


@pytest.mark.integration
def test_a_raw_mpegts_becomes_a_probeable_mp4(tmp_path):
    """D1 : le TS brut traversait ensure_seekable intact, et le sondage mourait
    derriere. Il doit ressortir en MP4 indexe, debarrasse des flux en trop."""
    src = _ts_multiflux(tmp_path / "v123.mp4")
    assert remux.is_mpegts(src) is True
    assert _flux(src) == {"video": 2, "audio": 2}
    avancement: list[float] = []
    assert ensure_seekable(src, duration_s=10.0, progress_cb=avancement.append) is True
    assert remux.is_mpegts(src) is False
    assert is_fragmented(src) is False
    assert 9.0 <= probe_media(src).duration_s <= 11.0
    assert _flux(src) == {"video": 1, "audio": 1}
    assert avancement and avancement[-1] <= 100.0
    assert list(tmp_path.glob("*remux.tmp*")) == []


@pytest.mark.integration
def test_a_truncated_mpegts_remux_is_refused_and_the_source_kept(tmp_path):
    """Le controle de sortie du TS est une DUREE, pas une taille : le remux perd
    volontairement des flux, donc le rapport de taille ne dit plus rien."""
    src = _ts_multiflux(tmp_path / "v123.mp4")
    avant = src.read_bytes()
    with pytest.raises(RemuxError, match="tronque"):
        ensure_seekable(src, duration_s=600.0)      # 10 s produites pour 600 s annoncees
    assert src.read_bytes() == avant
    assert list(tmp_path.glob("*remux.tmp*")) == []


def _vod_avec_source(conn, source: Path) -> int:
    from clipforge import db as dbmod
    return dbmod.create_vod(conn, title="t", source_path=str(source))


def _step_remux(conn, vod_id, monkeypatch, tmp_path, source: Path) -> list:
    """Joue l'etape de remux en interceptant l'appel reel. Retourne les appels vus."""
    from clipforge import config
    from clipforge.pipeline import remux as remux_mod
    from clipforge.pipeline.steps import build_real_steps

    downloads = tmp_path / "downloads"
    downloads.mkdir(exist_ok=True)
    monkeypatch.setattr(config, "DOWNLOADS_DIR", downloads)
    appels: list = []
    monkeypatch.setattr(remux_mod, "ensure_seekable",
                        lambda src, **kw: appels.append(Path(src)))
    dict(build_real_steps())["remuxing"](conn, vod_id, {})
    return appels


def test_step_remux_never_rewrites_a_locally_imported_file(conn, monkeypatch, tmp_path):
    """Un import local designe un fichier de l'utilisateur : hors de portee."""
    externe = tmp_path / "ailleurs" / "v.mp4"
    externe.parent.mkdir(parents=True)
    _ecrire(externe, _mp4(_atom(b"moov", _atom(b"mvex")), _atom(b"moof")))
    vod_id = _vod_avec_source(conn, externe)
    assert _step_remux(conn, vod_id, monkeypatch, tmp_path, externe) == []


def test_step_remux_handles_a_downloaded_file(conn, monkeypatch, tmp_path):
    telechargee = tmp_path / "downloads" / "v123.mp4"
    telechargee.parent.mkdir(parents=True, exist_ok=True)
    _ecrire(telechargee, _mp4(_atom(b"moov", _atom(b"mvex")), _atom(b"moof")))
    vod_id = _vod_avec_source(conn, telechargee)
    assert _step_remux(conn, vod_id, monkeypatch, tmp_path, telechargee) == [telechargee]


def test_step_remux_skips_a_vod_without_file(conn, monkeypatch, tmp_path):
    vod_id = _vod_avec_source(conn, tmp_path / "downloads" / "absente.mp4")
    assert _step_remux(conn, vod_id, monkeypatch, tmp_path, tmp_path) == []


def _step_remux_reel(conn, vod_id, monkeypatch, tmp_path) -> None:
    """Joue l'etape de remux SANS intercepter ffmpeg : c'est le remux reel qui juge."""
    from clipforge import config
    from clipforge.pipeline.steps import build_real_steps

    monkeypatch.setattr(config, "DOWNLOADS_DIR", tmp_path / "downloads")
    dict(build_real_steps())["remuxing"](conn, vod_id, {})


def _vod_telechargee(conn, source: Path, *, twitch_url: str | None) -> int:
    from clipforge import db as dbmod

    vod_id = dbmod.create_vod(conn, title=source.stem, source_path=str(source),
                              twitch_url=twitch_url)
    conn.execute("UPDATE vods SET duration_s=? WHERE id=?", (10.0, vod_id))
    conn.commit()
    return vod_id


@pytest.mark.integration
def test_a_truncated_ts_really_defeats_the_remux(tmp_path):
    """La fixture est genuinement mauvaise, et c'est ce qui donne sa valeur aux
    deux tests suivants : ffmpeg ne proteste pas, il copie ce qu'il peut et rend
    la main. Seul le controle de duree voit la troncature."""
    src = _ts_tronque(tmp_path / "v123.mp4")
    assert remux.is_mpegts(src) is True
    avant = src.read_bytes()
    with pytest.raises(remux.SourceInexploitableError, match="tronque"):
        ensure_seekable(src, duration_s=10.0)
    assert src.read_bytes() == avant
    assert list(tmp_path.glob("*remux.tmp*")) == []


@pytest.mark.integration
def test_a_corrupt_download_is_set_aside_so_a_fresh_one_can_heal_it(conn, tmp_path,
                                                                    monkeypatch):
    """D8 : le remux ne converge pas, et step_download rend la main des que
    source_path est renseigne — aucun geste de l'interface ne permettait de
    retelecharger, la VOD restait bloquee sur un fichier ni reparable ni
    remplacable. Ecarter le fichier et vider source_path rend la reprise capable
    de repartir du telechargement."""
    from clipforge import db as dbmod

    downloads = tmp_path / "downloads"
    downloads.mkdir()
    source = _ts_tronque(downloads / "v2836283171.mp4")
    octets = source.read_bytes()
    vod_id = _vod_telechargee(conn, source,
                              twitch_url="https://www.twitch.tv/videos/2836283171")

    with pytest.raises(remux.RemuxError, match="source ecartee"):
        _step_remux_reel(conn, vod_id, monkeypatch, tmp_path)

    assert not source.exists()                                   # le nom d'origine est libre
    ecarte = downloads / "v2836283171.corrompu.mp4"
    assert ecarte.read_bytes() == octets                         # rien n'est supprime
    assert dbmod.get_vod(conn, vod_id)["source_path"] == ""


@pytest.mark.integration
def test_a_source_that_cannot_be_redownloaded_stays_exactly_where_it_is(conn, tmp_path,
                                                                        monkeypatch):
    """Sans twitch_url, il n'y a rien a retelecharger : ecarter le fichier
    priverait l'utilisateur de sa seule copie sans rien lui rendre. Le diagnostic
    reste le sien, la source reste a sa place."""
    from clipforge import db as dbmod

    downloads = tmp_path / "downloads"
    downloads.mkdir()
    source = _ts_tronque(downloads / "capture.mp4")
    octets = source.read_bytes()
    vod_id = _vod_telechargee(conn, source, twitch_url=None)

    with pytest.raises(remux.RemuxError, match="tronque"):
        _step_remux_reel(conn, vod_id, monkeypatch, tmp_path)

    assert source.read_bytes() == octets
    assert list(downloads.glob("*corrompu*")) == []
    assert dbmod.get_vod(conn, vod_id)["source_path"] == str(source)


@pytest.mark.integration
def test_locked_source_is_kept_and_leaves_no_debris(tmp_path, monkeypatch):
    """Fichier verrouille par un autre programme : message clair, zero .tmp de plusieurs Go."""
    frag = tmp_path / "frag.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", str(FIXTURE), "-t", "5", "-c", "copy",
         "-movflags", "frag_keyframe+empty_moov", str(frag)], check=True)
    avant = frag.read_bytes()

    def replace_refuse(self, cible):
        raise PermissionError(32, "utilise par un autre processus")

    monkeypatch.setattr(Path, "replace", replace_refuse)
    with pytest.raises(RemuxError, match="verrouille"):
        ensure_seekable(frag)
    assert frag.read_bytes() == avant
    assert list(tmp_path.glob("*remux.tmp*")) == []
