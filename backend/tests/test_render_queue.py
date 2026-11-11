"""L'orchestration d'un rendu : ce que render_clip_job lit, ecrit et lance.

ffmpeg est remplace par un espion : ce qui est verifie ici, c'est la COMMANDE
assemblee a partir de la base (mots, signaux, reglages, accroche, emoji), pas le
fichier produit — ce dernier est le sujet du test d'integration de
test_ffmpeg_cmd.py.
"""
import json
import re
from pathlib import Path

from clipforge import db as dbmod
from clipforge.render import naming as naming_mod
from clipforge.render import queue as queue_mod
from clipforge.render.queue import render_clip_job

PROFILE = (0, 0, 640, 340, 640, 0, 868, 1080)
HOOK = "IL MANQUE DE MOURIR"


def _source(db_path: Path) -> Path:
    """La source que _seed pose a cote de la base."""
    return db_path.parent / "source.mp4"


def _seed(db_path: Path, *, fps: float | None = 30.0, hook: str | None = HOOK,
          emoji: str | None = "💀", signals: bool = True, words_json: str | None = None,
          handle: str = "twitch.tv/pseudo", emojis_actifs: str = "1", montage: str = "0",
          source: Path | None = None) -> int:
    """Une VOD analysee, un profil de scene et un clip pret a rendre. Rend l'id du clip.

    montage="0" par defaut : ces tests couvrent l'assemblage du rendu (legendes,
    emoji, mix audio, repli nvenc/cpu), pas le moteur de coupe -- ils pinnent
    donc le repli V5 exact (Task 8). test_montage_plan.py couvre le moteur actif.

    La source par defaut est un fichier vide pose a cote de la base, comme
    dans test_montage_plan.py : le rendu la SONDE maintenant (duree attendue
    bornee par la fin reelle de ses flux), et tests/fixtures/fixture.mp4 est
    ignoree par git -- absente en CI, la sonde echouerait la et pas ici. Un
    rendu REEL passe sa propre source, qu'il a construite.
    """
    if source is None:
        source = _source(db_path)
        source.write_bytes(b"")
    conn = dbmod.get_db(db_path)
    try:
        vod_id = dbmod.create_vod(conn, title="vod", source_path=str(source))
        conn.execute("UPDATE vods SET width=1920, height=1080, fps=? WHERE id=?",
                     (fps, vod_id))
        conn.executemany(
            "INSERT INTO words (vod_id, idx, start_s, end_s, text, probability) "
            "VALUES (?,?,?,?,?,?)",
            [(vod_id, i, 6.0 + i * 0.5, 6.4 + i * 0.5, mot, 0.9)
             for i, mot in enumerate(("il", "manque", "de", "mourir"))])
        if signals:
            conn.executemany(
                "INSERT INTO signals (vod_id, kind, t_s, score) VALUES (?,?,?,?)",
                [(vod_id, "audio", float(t), 0.9 if t == 8 else 0.1) for t in range(30)])
        cur = conn.execute(
            "INSERT INTO scene_profiles (name, cam_x, cam_y, cam_w, cam_h, "
            "game_x, game_y, game_w, game_h) VALUES ('scene',?,?,?,?,?,?,?,?)", PROFILE)
        profile_id = cur.lastrowid
        cur = conn.execute(
            "INSERT INTO clips (vod_id, start_s, end_s, title, profile_id, "
            "words_json, origin, hook, emoji) VALUES (?,?,?,?,?,?,?,?,?)",
            (vod_id, 5.0, 15.0, "mon clip", profile_id, words_json,
             "auto" if hook else "user", hook, emoji))
        clip_id = cur.lastrowid
        dbmod.set_setting(conn, "channel_handle", handle)
        dbmod.set_setting(conn, "captions_emojis", emojis_actifs)
        dbmod.set_setting(conn, "montage", montage)
        conn.commit()
        return clip_id
    finally:
        conn.close()


def _rendre(tmp_path: Path, clip_id: int) -> None:
    render_clip_job(clip_id, str(tmp_path / "clipforge.db"), str(tmp_path / "jobs"),
                    str(tmp_path / "output"))


def _clip(tmp_path: Path, clip_id: int):
    conn = dbmod.get_db(tmp_path / "clipforge.db")
    try:
        return conn.execute("SELECT * FROM clips WHERE id=?", (clip_id,)).fetchone()
    finally:
        conn.close()


def _ass(tmp_path: Path, clip_id: int) -> str:
    return (tmp_path / "jobs" / f"clip{clip_id}" / "captions.ass").read_text(encoding="utf-8")


def test_render_assembles_captions_punch_emoji_and_mixed_audio(tmp_path, ffmpeg):
    clip_id = _seed(tmp_path / "clipforge.db")
    _rendre(tmp_path, clip_id)

    ass = _ass(tmp_path, clip_id)
    assert ",Pop," in ass and ",Hook," in ass and ",Sign," in ass
    assert "MOURIR" in ass and "TWITCH.TV/PSEUDO" in ass
    assert "Karaoke" not in ass

    fc = ffmpeg.filter_complex
    assert "zoompan=z='1+0.22*(between(it,2.800,4.200))'" in fc and "fps=30.0" in fc
    assert "scale=160:-1" in fc and "overlay=" in fc
    assert "enable='between(t,0.000,2.800)'" in fc
    assert "loudnorm" in fc and "amix=inputs=" in fc
    cmd = ffmpeg.calls[-1]
    assert cmd[cmd.index("-map") + 1] == "[v]" and "[aout]" in cmd

    clip = _clip(tmp_path, clip_id)
    assert clip["status"] == "done" and clip["progress"] == 100
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2} - mon clip\.mp4",
                        Path(clip["output_path"]).name)


def test_missing_source_fps_falls_back_without_crashing(tmp_path, ffmpeg, caplog):
    clip_id = _seed(tmp_path / "clipforge.db", fps=None)
    with caplog.at_level("WARNING"):
        _rendre(tmp_path, clip_id)
    assert "fps=24.0" in ffmpeg.filter_complex
    assert "fps" in caplog.text.lower()
    assert _clip(tmp_path, clip_id)["status"] == "done"


def test_emoji_setting_off_drops_the_overlay(tmp_path, ffmpeg):
    clip_id = _seed(tmp_path / "clipforge.db", emojis_actifs="0")
    _rendre(tmp_path, clip_id)
    assert "overlay" not in ffmpeg.filter_complex
    joined = " ".join(ffmpeg.calls[-1])
    assert ".png" not in joined and "-loop" not in joined   # aucune entree image


def test_emoji_outside_the_vendored_set_is_ignored(tmp_path, ffmpeg):
    clip_id = _seed(tmp_path / "clipforge.db", emoji="🚀")
    _rendre(tmp_path, clip_id)
    assert "overlay" not in ffmpeg.filter_complex
    assert _clip(tmp_path, clip_id)["status"] == "done"


def test_a_flat_vod_gets_no_punch_and_no_zoompan(tmp_path, ffmpeg):
    clip_id = _seed(tmp_path / "clipforge.db", signals=False)
    _rendre(tmp_path, clip_id)
    assert "zoompan" not in ffmpeg.filter_complex


def test_a_user_clip_without_hook_gets_no_banner_and_no_impact(tmp_path, ffmpeg):
    clip_id = _seed(tmp_path / "clipforge.db", hook=None, emoji=None)
    _rendre(tmp_path, clip_id)
    assert ",Hook," not in _ass(tmp_path, clip_id)
    joined = " ".join(ffmpeg.calls[-1])
    assert "impact.wav" not in joined and "whoosh.wav" in joined
    # un export de l'editeur garde l'id du clip : deux exports du meme candidat
    # doivent rester distinguables dans le dossier de sortie
    assert _clip(tmp_path, clip_id)["output_path"].endswith(f"mon clip [c{clip_id}].mp4")


def test_the_auto_output_name_is_dated_and_transliterated():
    """Ce que l'utilisateur retrouve dans son dossier : AAAA-MM-JJ - titre.mp4.

    Les accents tombent, la ponctuation aussi, et les espaces laisses derriere
    elle sont recolles — sinon le nom porte les trous de la ponctuation retiree.
    """
    nom = naming_mod.output_name("Il manque de mourir : «très» fort?!")
    assert nom.endswith(" - Il manque de mourir tres fort.mp4")
    assert re.match(r"^\d{4}-\d{2}-\d{2} - ", nom)


def _clone_de_clip(db_path: Path, clip_id: int) -> int:
    """Un second clip identique au premier : meme VOD, meme titre, meme profil."""
    conn = dbmod.get_db(db_path)
    try:
        clip = conn.execute("SELECT * FROM clips WHERE id=?", (clip_id,)).fetchone()
        cur = conn.execute(
            "INSERT INTO clips (vod_id, start_s, end_s, title, profile_id, origin, hook, emoji) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (clip["vod_id"], clip["start_s"], clip["end_s"], clip["title"],
             clip["profile_id"], clip["origin"], clip["hook"], clip["emoji"]))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def test_a_second_clip_with_the_same_title_never_overwrites_the_first(tmp_path, ffmpeg):
    """Deux titres identiques le meme jour visaient le meme fichier.

    Le second rendu ecrasait le premier : deux lignes 'done' en base, un seul
    fichier sur le disque, et rien nulle part pour le dire. Un montage reussi ne
    se perd pas en silence — le second prend un rang.
    """
    premier = _seed(tmp_path / "clipforge.db")
    second = _clone_de_clip(tmp_path / "clipforge.db", premier)
    _rendre(tmp_path, premier)
    _rendre(tmp_path, second)
    chemins = [_clip(tmp_path, c)["output_path"] for c in (premier, second)]
    assert chemins[0] != chemins[1]
    assert all(Path(p).is_file() for p in chemins)
    assert Path(chemins[1]).name.endswith(" - mon clip (2).mp4")


def test_re_rendering_a_clip_rewrites_its_own_file(tmp_path, ffmpeg):
    """Une retouche rejouee reecrit SON fichier : pas de (2), (3), (4)...

    C'est la contrepartie du rang : sans elle, chaque passage dans l'editeur
    laisserait une copie de plus dans le dossier de sortie.
    """
    clip_id = _seed(tmp_path / "clipforge.db")
    _rendre(tmp_path, clip_id)
    premier = _clip(tmp_path, clip_id)["output_path"]
    _rendre(tmp_path, clip_id)
    assert _clip(tmp_path, clip_id)["output_path"] == premier
    assert len(list((tmp_path / "output").iterdir())) == 1


def test_safe_filename_never_returns_an_empty_name():
    assert naming_mod.safe_filename("?!:", "clip42") == "clip42"
    assert naming_mod.safe_filename("", "clip42") == "clip42"


def test_safe_filename_is_bounded_in_length():
    long = naming_mod.safe_filename("mot " * 60, "clip42")
    assert len(long) <= naming_mod.FILENAME_MAX_CHARS
    assert not long.endswith(" ")


def test_edited_words_outside_the_clip_are_not_captioned(tmp_path, ffmpeg):
    """words_json est edite par l'utilisateur : rien ne le borne au clip, et un
    groupe pop ne clampe QUE sa fin — un mot d'apres la fin resterait a l'ecran.
    """
    mots = [{"start": 6.0, "end": 6.4, "text": "dedans"},
            {"start": 40.0, "end": 40.4, "text": "dehors"}]
    clip_id = _seed(tmp_path / "clipforge.db", words_json=json.dumps(mots))
    _rendre(tmp_path, clip_id)
    ass = _ass(tmp_path, clip_id)
    assert "DEDANS" in ass and "DEHORS" not in ass


def test_the_cpu_fallback_retries_the_render_without_nvenc(tmp_path, ffmpeg):
    ffmpeg.codes = [1, 0]
    clip_id = _seed(tmp_path / "clipforge.db")
    _rendre(tmp_path, clip_id)
    assert len(ffmpeg.calls) == 2
    assert "h264_nvenc" in ffmpeg.calls[0] and "libx264" in ffmpeg.calls[1]
    assert _clip(tmp_path, clip_id)["status"] == "done"


def test_a_render_that_fails_twice_lands_in_error(tmp_path, ffmpeg):
    ffmpeg.codes = [1]
    clip_id = _seed(tmp_path / "clipforge.db")
    _rendre(tmp_path, clip_id)
    clip = _clip(tmp_path, clip_id)
    assert clip["status"] == "error" and "ffmpeg" in clip["error"]


def test_the_render_call_is_bounded_in_time(tmp_path, ffmpeg):
    clip_id = _seed(tmp_path / "clipforge.db")
    _rendre(tmp_path, clip_id)
    assert ffmpeg.kwargs[-1]["timeout"] == queue_mod.RENDER_TIMEOUT_S


def test_a_render_that_never_returns_lands_in_error_not_in_limbo(tmp_path, ffmpeg):
    """Un ffmpeg qui ne rend jamais la main laissait le clip en 'rendering' pour
    l'eternite : aucune ligne d'erreur, rien de relancable depuis l'interface.

    Le cas n'est pas theorique : une entree `-loop 1` dont l'overlay perd son
    `shortest=1` produit un rendu SANS FIN (mesure : toujours vivant a 45 s sur
    un clip de 2 s). Un rendu borne qui echoue vaut mieux qu'un worker fige.
    """
    ffmpeg.expire = True
    clip_id = _seed(tmp_path / "clipforge.db")
    _rendre(tmp_path, clip_id)
    clip = _clip(tmp_path, clip_id)
    assert clip["status"] == "error"
    assert str(queue_mod.RENDER_TIMEOUT_S) in clip["error"]
    assert len(ffmpeg.calls) == 2      # nvenc puis cpu, puis on abandonne


def test_le_nom_de_fichier_herite_de_la_jonction_des_mots():
    """naming.py n'a pas a connaitre les elisions : il recoit un titre deja
    recolle. La liste blanche retire l'apostrophe SANS laisser de trou, donc
    « Non Non c est pas moi » devient « Non Non cest pas moi ». Test de
    verrouillage : la chaine mots -> titre -> fichier ne doit plus se rouvrir."""
    from clipforge.text import join_words

    titre = join_words(["Non", "!", "Non,", "c", "'est", "pas", "moi"])
    assert naming_mod.safe_filename(titre, "clip") == "Non Non cest pas moi"
