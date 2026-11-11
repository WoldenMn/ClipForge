"""Ce que ClipForge fait d'un fichier que l'utilisateur lui designe, sans jamais
le reecrire.

Un import local en MPEG-TS brut ne se seeke pas : `-ss X -i source` y rend
l'image de la cle SUIVANTE, pendant que l'audio part bien de X. Mesure du
2026-09-20 sur une source a GOP de 4,167 s : `-ss 21.337` sortait l'image de
25.021 s, soit une video en avance de 3684 ms sous un audio juste, rc=0 ; et
`-ss 38.3` ne sortait aucune image du tout. Le meme flux en MP4 indexe seeke a
0 ms, et un MP4 fragmente sans index a 25 ms pres — le defaut tient au seul
conteneur TS.

La reparation lui donne une copie indexee dans le dossier de donnees. L'original
reste intact : il appartient a l'utilisateur.
"""
import subprocess
from pathlib import Path

import pytest

from clipforge import db as dbmod
from clipforge.pipeline import remux
from clipforge.pipeline.steps import build_real_steps

GOP_S = 5.0          # 150 images a 30 fps : bien plus long qu'un clip de teaser
FPS = 30
SECONDE_SONDEE = 6   # instant seeke, au milieu du GOP qui court de 5 s a 10 s
NIVEAU_PAR_SECONDE = 20


def _ts_a_gop_long(dest: Path) -> Path:
    """Un MPEG-TS ou la luminance de l'image donne sa seconde source.

    Seconde 0 -> niveau 0, seconde 6 -> niveau 120 : lire une image suffit a
    dire de quel instant elle vient, sans faire confiance aux timestamps que le
    conteneur ecrit — ce sont eux qui mentent ici.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error",
         "-f", "lavfi", "-i", f"color=c=black:s=64x64:r={FPS}:d=12",
         "-f", "lavfi", "-i", "anoisesrc=d=12:c=white:r=48000:a=0.3:seed=7",
         "-vf", f"geq=lum='{NIVEAU_PAR_SECONDE}*floor(N/{FPS})':cb=128:cr=128",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
         "-x264-params", f"keyint={int(GOP_S * FPS)}:min-keyint={int(GOP_S * FPS)}:scenecut=0",
         "-c:a", "aac", "-b:a", "64k", "-f", "mpegts", str(dest)],
        check=True)
    return dest


def _niveaux_par_seconde(source: Path) -> list[int]:
    """Le niveau lu pour chacune des secondes de la source, sans aucun seek.

    Sert d'etalon a _seconde_vue_au_seek plutot qu'un calcul sur papier : la
    chaine de lecture convertit la plage TV en plage PC (un niveau pose a 200
    ressort a 214), et comparer a une valeur theorique ferait echouer la sonde
    pour une raison etrangere au defaut mesure.
    """
    proc = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(source), "-vf", "fps=1,scale=1:1",
         "-pix_fmt", "gray", "-f", "rawvideo", "-"],
        capture_output=True, check=True)
    assert len(proc.stdout) >= 12, "source d'etalonnage trop courte"
    return list(proc.stdout)


def _seconde_vue_au_seek(source: Path, instant: float, etalon: list[int]) -> int:
    """La seconde source que rend un seek d'entree, lue dans l'image elle-meme.

    Exactement la disposition de build_render_cmd : `-ss` pose AVANT `-i`.
    """
    proc = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", str(instant), "-i", str(source),
         "-frames:v", "1", "-vf", "scale=1:1", "-pix_fmt", "gray",
         "-f", "rawvideo", "-"],
        capture_output=True, check=True)
    assert proc.stdout, f"aucune image rendue a {instant} s"
    niveau = proc.stdout[0]
    return min(range(len(etalon)), key=lambda s: abs(etalon[s] - niveau))


def _vod_importee(conn, source: Path) -> int:
    """Une vod telle que /import-local la cree : pas de twitch_url."""
    vod_id = dbmod.create_vod(conn, title=source.stem, source_path=str(source))
    conn.execute("UPDATE vods SET duration_s=? WHERE id=?", (12.0, vod_id))
    conn.commit()
    return vod_id


def _mp4_fragmente(dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    fixture = Path(__file__).parent / "fixtures" / "fixture.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(fixture), "-t", "5",
                    "-c", "copy", "-movflags", "frag_keyframe+empty_moov", str(dest)],
                   check=True)
    return dest


def test_a_locally_imported_mpegts_is_repaired_as_a_copy(tmp_path):
    """Le TS importe doit etre repare, et par une COPIE : le rendu serait faux
    sinon, et l'original ne nous appartient pas."""
    externe = tmp_path / "ailleurs" / "stream.ts"
    externe.parent.mkdir(parents=True)
    externe.write_bytes(bytes([remux.TS_SYNC]) + b"\x00" * (remux.TS_PACKET - 1))
    externe.write_bytes(externe.read_bytes() * 8)
    plan = remux.plan_de_remux(externe, tmp_path / "downloads")
    assert plan is not None and plan.defaut == "mpegts"
    assert plan.en_copie is True


def test_a_downloaded_container_is_repaired_in_place(tmp_path):
    """Un fichier telecharge par ClipForge lui appartient : il se remplace."""
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    telechargee = downloads / "v123.mp4"
    telechargee.write_bytes((bytes([remux.TS_SYNC]) + b"\x00" * (remux.TS_PACKET - 1)) * 8)
    plan = remux.plan_de_remux(telechargee, downloads)
    assert plan is not None and plan.defaut == "mpegts"
    assert plan.en_copie is False


@pytest.mark.integration
def test_a_locally_imported_fragmented_mp4_is_left_alone(tmp_path):
    """Mesure du 2026-09-20 : un MP4 fragmente sans index seeke a 25 ms pres.
    Rien a reparer pour le rendu, donc rien a copier — son defaut ne gene que
    l'apercu du navigateur, ce qui ne vaut pas de dupliquer des gigaoctets."""
    externe = _mp4_fragmente(tmp_path / "ailleurs" / "v.mp4")
    assert remux.is_fragmented(externe) is True
    assert remux.plan_de_remux(externe, tmp_path / "downloads") is None


@pytest.mark.integration
def test_the_probe_sees_the_ts_seek_defect_before_anything_is_repaired(tmp_path):
    """Le cas connu-mauvais, mesure AVANT la reparation.

    Sans lui, les deux tests suivants passeraient meme si la sonde etait aveugle
    (une sonde qui rendrait toujours la bonne seconde serait indiscernable d'un
    succes). Ici la source brute DOIT mentir.
    """
    brut = _ts_a_gop_long(tmp_path / "ailleurs" / "stream.ts")
    vue = _seconde_vue_au_seek(brut, SECONDE_SONDEE + 0.3, _niveaux_par_seconde(brut))
    assert vue != SECONDE_SONDEE
    assert vue == int(GOP_S * 2)                          # la cle suivante, a 10 s


@pytest.mark.integration
def test_step_remux_hands_the_pipeline_a_ts_source_that_seeks_true(conn, monkeypatch,
                                                                  tmp_path):
    """Le defaut d'origine : apres l'etape, un seek d'entree doit rendre l'image
    de l'instant demande, et non celle de la cle suivante."""
    from clipforge import config

    monkeypatch.setattr(config, "DOWNLOADS_DIR", tmp_path / "downloads")
    brut = _ts_a_gop_long(tmp_path / "ailleurs" / "stream.ts")
    etalon = _niveaux_par_seconde(brut)
    vod_id = _vod_importee(conn, brut)

    dict(build_real_steps())["remuxing"](conn, vod_id, {})

    rendue = Path(dbmod.get_vod(conn, vod_id)["source_path"])
    assert _seconde_vue_au_seek(rendue, SECONDE_SONDEE + 0.3, etalon) == SECONDE_SONDEE


@pytest.mark.integration
def test_step_remux_never_touches_the_imported_file_itself(conn, monkeypatch, tmp_path):
    """La copie vit dans le dossier de donnees ; l'original reste a l'utilisateur,
    octet pour octet."""
    from clipforge import config

    downloads = tmp_path / "downloads"
    monkeypatch.setattr(config, "DOWNLOADS_DIR", downloads)
    brut = _ts_a_gop_long(tmp_path / "ailleurs" / "stream.ts")
    avant = brut.read_bytes()
    vod_id = _vod_importee(conn, brut)

    dict(build_real_steps())["remuxing"](conn, vod_id, {})

    assert brut.read_bytes() == avant
    rendue = Path(dbmod.get_vod(conn, vod_id)["source_path"])
    assert rendue != brut
    assert rendue.parent == downloads
    assert remux.is_mpegts(rendue) is False
    assert list(tmp_path.rglob("*remux.tmp*")) == []


@pytest.mark.integration
def test_a_second_analysis_reuses_the_copy_instead_of_remaking_it(conn, monkeypatch,
                                                                  tmp_path):
    """La reprise ne doit pas repayer la copie : une VOD de plusieurs Go se
    recopierait a chaque relance."""
    from clipforge import config

    monkeypatch.setattr(config, "DOWNLOADS_DIR", tmp_path / "downloads")
    brut = _ts_a_gop_long(tmp_path / "ailleurs" / "stream.ts")
    vod_id = _vod_importee(conn, brut)
    etape = dict(build_real_steps())["remuxing"]

    etape(conn, vod_id, {})
    copie = Path(dbmod.get_vod(conn, vod_id)["source_path"])
    empreinte = copie.stat().st_mtime_ns

    etape(conn, vod_id, {})

    assert Path(dbmod.get_vod(conn, vod_id)["source_path"]) == copie
    assert copie.stat().st_mtime_ns == empreinte
