"""L'orchestrateur de montage (Task 8), moteur V6 : cablages de
render.montage_plan.build_montage_options -- coupes, grille, teaser, emphase,
secousses, duree de sortie.

Les scenarios partages (trou muet, eclats, `_seed_gap_scenario`) vivent ici et
les autres fichiers de l'orchestrateur les importent : repli V5 exact dans
test_montage_plan_v5.py, sous-titres face aux coupes dans
test_montage_plan_sous_titres.py, reglages, entrees et replis dans
test_montage_plan_replis.py.

Reutilise les helpers de test_render_queue.py (DB temporaire seedee) et la
fixture `ffmpeg` (ffmpeg entierement simule, definie dans conftest.py) :
aucun test ici ne fait tourner ffmpeg ni une extraction audio reelle.
"""
import re
from pathlib import Path

import numpy as np
import pytest
from test_render_queue import PROFILE, _ass, _clip, _rendre, _seed, _source

from clipforge import db as dbmod
from clipforge.models import SubtitleStyle
from clipforge.pipeline.segmenter import Word
from clipforge.render import montage_plan as montage_plan_mod
from clipforge.render import queue as queue_mod
from clipforge.render import sfx as sfx_mod
from clipforge.render.montage.edl import build_edl, choose_cold_open
from clipforge.render.montage.emphasis import SPREAD_MIN_DB, word_tiers
from clipforge.render.montage.energy import fine_energy, spread_db
from clipforge.render.montage.motion import (
    PunchWindow,
    build_motion,
    detect_punches,
)
from clipforge.render.montage.timemap import TimeMap
from clipforge.render.montage.words_out import relative_words

_ASS_TIME_RE = re.compile(r"(\d+):(\d{2}):(\d{2})\.(\d{2})")


def _ass_times_s(ass_text: str) -> list[float]:
    """Tous les horodatages 'H:MM:SS.cc' d'un fichier ASS, en secondes."""
    return [int(h) * 3600 + int(m) * 60 + int(s) + int(cs) / 100
            for h, m, s, cs in _ASS_TIME_RE.findall(ass_text)]


def _gap_words() -> list[tuple[float, float, str]]:
    """19 mots relatifs au clip (12 s) : un seul trou candidat, 2,0 s de silence
    entre le 4e et le 5e mot -- tous les autres ecarts restent sous le seuil
    "standard" (0,55 s) et ne sont donc jamais des candidats a la coupe."""
    mots = []
    t = 0.2
    for i in range(4):
        mots.append((round(t, 3), round(t + 0.4, 3), f"mot{i}"))
        t += 0.5
    t = 4.1
    for i in range(4, 19):
        mots.append((round(t, 3), round(t + 0.4, 3), f"mot{i}"))
        t += 0.5
    return mots


def _synthetic_energy_wav(_path: Path) -> tuple[np.ndarray, int]:
    """12 s de sinusoide (parole simulee), silence total entre 2,0 s et 4,2 s
    -- couvre largement le trou mot4->mot5 (2,1 a 4,1) une fois pade de 0,12 s."""
    sr, dur = 16000, 12.0
    t = np.arange(int(dur * sr)) / sr
    x = 0.3 * np.sin(2 * np.pi * 300 * t)
    x[(t >= 2.0) & (t < 4.2)] = 0.0
    return x.astype(np.float32), sr


def _parole_avec_eclat(debut: float, fin: float):
    """read_wav_mono de substitution : la parole du trou muet, plus un eclat a
    0,95 (-3,5 dB contre -13,5 dB pour la parole) sur [debut, fin)."""
    def _lire(path: Path) -> tuple[np.ndarray, int]:
        x, sr = _synthetic_energy_wav(path)
        t = np.arange(len(x)) / sr
        fort = (t >= debut) & (t < fin)
        x[fort] = 0.95 * np.sin(2 * np.pi * 300 * t[fort])
        return x, sr
    return _lire


def _seed_gap_scenario(db_path: Path, *, montage: str = "1", fps: float | None = 30.0,
                       signaux: list[tuple[float, float]] | None = None,
                       mots: list[tuple[float, float, str]] | None = None) -> int:
    """Une VOD de 19 mots sur un clip de 12 s, un profil, un clip sans hook ni
    emoji. `signaux` : serie audio grossiere (t_s absolu, score) de la VOD,
    aucune par defaut.

    La source est un fichier vide pose a cote de la base : le vrai extract_wav
    n'en verifie que la presence, FauxFfmpeg fait l'extraction et chaque test
    patche read_wav_mono. Pointee sur tests/fixtures/fixture.mp4 (ignoree par
    git, generee seulement si ffmpeg est la), elle manquait en CI : extraction
    en echec, EDL plein, et les tests tombaient avant leurs assertions de
    contrat (revue finale V6, run 34514694811)."""
    source = db_path.parent / "source.mp4"
    source.write_bytes(b"")
    conn = dbmod.get_db(db_path)
    try:
        vod_id = dbmod.create_vod(conn, title="vod", source_path=str(source))
        conn.execute("UPDATE vods SET width=1920, height=1080, fps=? WHERE id=?", (fps, vod_id))
        conn.executemany(
            "INSERT INTO words (vod_id, idx, start_s, end_s, text, probability) "
            "VALUES (?,?,?,?,?,?)",
            [(vod_id, i, 100.0 + s, 100.0 + e, txt, 0.9)
             for i, (s, e, txt) in enumerate(mots or _gap_words())])
        conn.executemany(
            "INSERT INTO signals (vod_id, kind, t_s, score) VALUES (?,?,?,?)",
            [(vod_id, "audio", t, score) for t, score in signaux or []])
        cur = conn.execute(
            "INSERT INTO scene_profiles (name, cam_x, cam_y, cam_w, cam_h, "
            "game_x, game_y, game_w, game_h) VALUES ('scene',?,?,?,?,?,?,?,?)", PROFILE)
        profile_id = cur.lastrowid
        cur = conn.execute(
            "INSERT INTO clips (vod_id, start_s, end_s, title, profile_id, origin) "
            "VALUES (?,?,?,?,?,?)", (vod_id, 100.0, 112.0, "clip trou", profile_id, "user"))
        clip_id = cur.lastrowid
        dbmod.set_setting(conn, "montage", montage)
        conn.commit()
        return clip_id
    finally:
        conn.close()


def test_montage_actif_avec_trou_muet_coupe_et_borne_les_temps(tmp_path, ffmpeg, monkeypatch):
    """Un trou muet de 2 s entre deux mots doit produire une vraie coupe EDL :
    video_cut non vide, aucun temps ASS au-dela de la duree de sortie, et la
    duree -t de la commande ffmpeg reste celle du CLIP SOURCE (build_render_cmd
    ne raccourcit jamais son entree : seul le filter_complex localise les
    coupes en aval).

    Espionne aussi measure_loudness (au lieu du lambda muet de la fixture
    `ffmpeg`, qui avale n'importe quel kwarg) pour figer le contrat le plus
    fragile de l'orchestrateur (finding 1, revue Task 8) : sur le chemin
    coupe, measure_loudness DOIT recevoir le graphe `audio_cut`, ET le mix
    audio emis DOIT lire `[acut]` -- sinon la loudness mesuree porte sur un
    audio qui ne sort jamais du rendu.
    """
    appels_loudness: list[dict] = []
    appels_build_cmd: list[dict] = []

    def _mesure_espion(*args, **kwargs):
        appels_loudness.append(kwargs)

    reel_build_render_cmd = queue_mod.build_render_cmd

    def _build_cmd_espion(*args, **kwargs):
        appels_build_cmd.append(kwargs)
        return reel_build_render_cmd(*args, **kwargs)

    monkeypatch.setattr(montage_plan_mod, "read_wav_mono", _synthetic_energy_wav)
    monkeypatch.setattr(montage_plan_mod, "measure_loudness", _mesure_espion)
    monkeypatch.setattr(queue_mod, "build_render_cmd", _build_cmd_espion)
    db_path = tmp_path / "clipforge.db"
    clip_id = _seed_gap_scenario(db_path)
    _rendre(tmp_path, clip_id)

    clip = _clip(tmp_path, clip_id)
    assert clip["status"] == "done", clip["error"]

    # Attendu independant, calcule avec les MEMES primitives (build_edl/TimeMap)
    # sur les memes mots et la meme energie synthetique que le pipeline vient
    # d'utiliser en interne.
    mots_rel = [Word(start=s, end=e, text=t) for s, e, t in _gap_words()]
    samples, sr = _synthetic_energy_wav(Path("ignore"))
    energy = fine_energy(samples, sr)
    edl_attendu = build_edl(mots_rel, energy, clip_dur_s=12.0, cut_style="standard")
    assert len(edl_attendu.segments) > 1, "le trou muet doit produire une vraie coupe"
    tmap_attendu = TimeMap.from_edl(edl_attendu)

    assert "concat=" in ffmpeg.filter_complex, "video_cut doit etre non vide (vraie coupe)"

    assert len(appels_loudness) == 1
    assert appels_loudness[0].get("audio_cut"), (
        "measure_loudness doit recevoir le graphe audio_cut (contrat coupe, finding 1)")

    # Assertion sur le graphe MIX lui-meme (options["audio_graph"], recu ici via
    # l'espion sur build_render_cmd) : seul endroit ou coupe correcte et coupe
    # cassee se distinguent. Le graphe audio_cut se termine TOUJOURS par le label
    # litteral [acut], meme quand le mix relit la source brute -- affirmer
    # "[acut] in ffmpeg.filter_complex" est donc decoratif (vrai dans les deux
    # cas, prouve par sonde, cf. rapport Task 8 fix round 2) et ne peut pas
    # detecter une regression qui reviendrait a src=[0:a].
    assert len(appels_build_cmd) == 1
    mix_graph = appels_build_cmd[0]["audio_graph"]
    assert mix_graph.startswith("[acut]"), (
        "le mix audio remis a build_render_cmd doit COMMENCER par le flux coupe "
        "[acut] (contrat coupe, finding 1)")
    assert "[0:a]" not in mix_graph, (
        "le mix audio ne doit jamais retomber sur la source brute [0:a] cote "
        "coupe (contrat coupe, finding 1)")

    ass = _ass(tmp_path, clip_id)
    assert max(_ass_times_s(ass)) <= tmap_attendu.output_duration + 1e-6

    cmd = ffmpeg.calls[-1]
    assert cmd[cmd.index("-t") + 1] == "12.0"
    # Revue A2 (integration M1) : le graphe V6 passe par un fichier du job, la
    # ligne de commande depassait 32 767 caracteres vers 105 segments.
    fichier = cmd[cmd.index("-/filter_complex") + 1]
    assert "-filter_complex" not in cmd
    assert Path(fichier).parent == tmp_path / "jobs" / f"clip{clip_id}"


def test_chaque_fps_du_rendu_v6_porte_la_grille_de_zoompan(tmp_path, ffmpeg, monkeypatch):
    """Revue A2, fix round 1 : zoompan ramene sa cadence par av_d2q(fps, 1001000),
    le filtre fps par av_d2q(fps, INT_MAX). Un meme texte flottant non rond
    (59.99998924..., avg_frame_rate 334800000/5580001) leur donnait deux grilles
    et vstack sortait deux images par instant. Chaque fps= de la commande V6
    (morceaux, sortie de concat, zoompan) porte le meme texte num/den, termes
    sous 1001000 : les deux filtres le relisent sans l'arrondir."""
    monkeypatch.setattr(montage_plan_mod, "read_wav_mono", _synthetic_energy_wav)
    clip_id = _seed_gap_scenario(tmp_path / "clipforge.db", fps=59.999989247313756)
    _rendre(tmp_path, clip_id)
    assert _clip(tmp_path, clip_id)["status"] == "done", _clip(tmp_path, clip_id)["error"]

    fc = ffmpeg.filter_complex
    textes = re.findall(r"\bfps=([^:,\[;]+)", fc)
    assert "zoompan=" in fc and "concat=" in fc and len(textes) >= 4, fc
    assert set(textes) == {"60/1"}, textes


def test_la_coupe_du_teaser_porte_le_whoosh_de_sfx_plan(tmp_path, ffmpeg, monkeypatch):
    """Revue finale V6 : le cablage teaser -> whoosh (teaser_cut_s de sfx_plan)
    tournait deja dans le test d'index du teaser (lot A, whoosh a 1,05 s pour
    une coupe a 1,2 s), mais ni lui ni le test d'integration a teaser n'en
    assertaient rien : teaser_cut_s = None, + 3.0 ou cut_times()[-1] passaient
    partout. Un eclat net sous un mot, apres 3 s, donne un teaser ; le trou
    muet ajoute une coupe du corps, sans laquelle [0] et [-1] seraient la meme
    coupe (c'est le cas du test d'index).

    Re-revue du lot B : aucun test a teaser ne touchait build_motion, et lui
    passer les coupes SANS celle du teaser survivait, alors que la parite du
    zoom bascule pour tout le corps. L'espion tient les coupes completes."""
    lire = _parole_avec_eclat(7.15, 7.4)
    appels: list[dict] = []
    coupes_du_mouvement: list[list[float]] = []
    reel_sfx_plan = montage_plan_mod.sfx_plan
    reel_build_motion = montage_plan_mod.build_motion

    def _sfx_plan_espion(*args, **kwargs):
        appels.append(kwargs)
        return reel_sfx_plan(*args, **kwargs)

    def _build_motion_espion(fenetres, coupes, **kwargs):
        coupes_du_mouvement.append(coupes)
        return reel_build_motion(fenetres, coupes, **kwargs)

    monkeypatch.setattr(montage_plan_mod, "read_wav_mono", lire)
    monkeypatch.setattr(montage_plan_mod, "sfx_plan", _sfx_plan_espion)
    monkeypatch.setattr(montage_plan_mod, "build_motion", _build_motion_espion)
    mots_rel = [Word(start=s, end=e, text=t) for s, e, t in _gap_words()]
    edl = build_edl(mots_rel, fine_energy(*lire(Path("ignore"))), clip_dur_s=12.0,
                    cut_style="standard")
    assert edl.teaser is not None, "l'eclat doit produire un teaser"
    coupes = TimeMap.from_edl(edl).cut_times()
    assert len(coupes) >= 2, coupes

    clip_id = _seed_gap_scenario(tmp_path / "clipforge.db")
    _rendre(tmp_path, clip_id)
    assert _clip(tmp_path, clip_id)["status"] == "done", _clip(tmp_path, clip_id)["error"]

    assert len(appels) == 1, appels
    assert appels[0]["teaser_cut_s"] == coupes[0] == round(
        edl.teaser.src_end - edl.teaser.src_start, 3), (appels, coupes, edl.teaser)
    assert coupes_du_mouvement == [coupes], (coupes_du_mouvement, coupes)


_PUNCH_A_106 = [(float(t), 0.9 if t == 106 else 0.1) for t in range(98, 115)]


def _mots_du_tier(ass: str, taille: int) -> list[str]:
    """Mots de l'ASS ouverts par le tag de taille \\fs<taille> d'un tier, dans
    l'ordre du fichier (couleur du mot-cle sautee)."""
    return re.findall(rf"\\fs{taille}[^}}]*\}}(?:\{{\\1c[^}}]*\}})?([^{{ ]+)", ass)


@pytest.mark.parametrize("fps", [30.0, 25.0, 60.0, 24.0])
def test_emphase_et_secousses_du_v6_suivent_l_energie_et_les_punches(
        tmp_path, ffmpeg, monkeypatch, fps):
    """Revue finale V6 (M3, M5) : tiers=None a l'appel de build_pop_ass, ou une
    secousse avancee de 5 s au lieu de 0,2, laissaient la CI et l'integration
    vertes. Le test d'index du teaser (lot A) rendait deja une fenetre de punch
    et sa secousse sans rien en asserter, et son energie plate a un pic laissait
    tous les mots "normal". Revue du lot B : l'inversion des tiers, leur
    decalage d'un mot, une secousse ou un zoom lus sur la fenetre brute
    survivaient encore. Ici deux secondes fortes sous les quatre premiers mots,
    avant TEASER_MIN_PEAK_T (aucun teaser), et un pic de signal a 106 s : la
    coupe du trou muet avance la fenetre de punch de 1,76 s, brute et projetee
    ne se confondent pas. Attendus calcules a part, avec les memes primitives,
    sur la meme EDL. Re-revue du lot B : a 30 ips seulement, passer fps=30.0 en
    dur a build_motion survivait ; 25 ips le distingue, 60 (la cadence Twitch
    courante) et 24 bornent un plafond ou un plancher glisses sur la cadence."""
    lire = _parole_avec_eclat(0.0, 2.0)
    monkeypatch.setattr(montage_plan_mod, "read_wav_mono", lire)
    db_path = tmp_path / "clipforge.db"
    clip_id = _seed_gap_scenario(db_path, signaux=_PUNCH_A_106, fps=fps)

    mots_rel = [Word(start=s, end=e, text=t) for s, e, t in _gap_words()]
    energy = fine_energy(*lire(Path("ignore")))
    assert spread_db(energy) >= SPREAD_MIN_DB
    assert word_tiers(mots_rel, energy) == ["haut"] * 4 + ["bas"] * 15
    edl = build_edl(mots_rel, energy, clip_dur_s=12.0, cut_style="standard")
    assert edl.teaser is None and len(edl.segments) == 2, edl
    tmap = TimeMap.from_edl(edl)
    brutes = detect_punches(_PUNCH_A_106, clip_start=100.0, clip_end=112.0)
    projetees = [tmap.map_window(w.start, w.end) for w in brutes]
    assert projetees and None not in projetees, projetees
    fenetres = [PunchWindow(*p) for p in projetees]
    assert fenetres != brutes, fenetres

    conn = dbmod.get_db(db_path)
    try:
        clip = conn.execute("SELECT * FROM clips WHERE id=?", (clip_id,)).fetchone()
        job_dir = tmp_path / "jobs" / f"clip{clip_id}"
        job_dir.mkdir(parents=True)
        options = montage_plan_mod.build_montage_options(
            conn, clip, dbmod.get_vod(conn, clip["vod_id"]), job_dir)
    finally:
        conn.close()

    ass = (job_dir / montage_plan_mod.ASS_NAME).read_text(encoding="utf-8")
    taille = SubtitleStyle().font_size
    assert _mots_du_tier(ass, round(1.35 * taille)) == [f"MOT{i}" for i in range(4)], ass
    assert _mots_du_tier(ass, round(0.85 * taille)) == [f"MOT{i}" for i in range(4, 19)], ass

    attendu = build_motion(fenetres, tmap.cut_times(),
                           shake_at=[w.start + 0.2 for w in fenetres], fps=fps)
    assert (options["zoom_expr"], options["x_expr"], options["y_expr"]) == (
        attendu.zoom_expr, attendu.x_expr, attendu.y_expr)


_VC0_RE = re.compile(r"\[(\d+):v\]trim=end=([\d.]+),[^;]*\[vc0\]")
_AC0_RE = re.compile(r"\[(\d+):a\]atrim=start=[\d.]+,asetpts=PTS-STARTPTS\[ac0\]")
_EMOJI_RE = re.compile(r"\[(\d+):v\]scale=160:-1\[emo\]")


def _energie_pic_a_6s(_samples, _sr) -> list[tuple[float, float]]:
    """Energie plate a -50 dB sur 10 s, pic a 0 dB a 6,0 s (relatif au clip)."""
    return [(round(i * 0.05, 3), 0.0 if i == 120 else -50.0) for i in range(200)]


def _arguments_entree(cmd: list[str], k: int) -> list[str]:
    """Arguments de la k-ieme entree de `cmd` (comptee sur les vrais -i), son
    chemin compris : tout ce qui suit le chemin de l'entree k-1."""
    entrees = [i for i, arg in enumerate(cmd) if arg == "-i"]
    assert 0 < k < len(entrees), f"entree {k} absente : {len(entrees)} -i dans {cmd}"
    return cmd[entrees[k - 1] + 2: entrees[k] + 2]


def test_l_index_du_teaser_designe_son_entree_seekee_dans_le_rendu_et_la_mesure(
        tmp_path, ffmpeg, monkeypatch):
    """Revue finale V6 : le teaser lit sa propre entree seekee, placee apres les
    effets et avant l'emoji. Un index decale d'un cran lirait un effet, l'emoji
    ou rien du tout. Scenario complet (teaser, effets, emoji), commandes REELLES
    (rendu via render_clip_job, mesure via le vrai measure_loudness), -i comptes
    sur la liste de la commande elle-meme."""
    monkeypatch.setattr(montage_plan_mod, "extract_wav", lambda *a, **k: tmp_path / "x.wav")
    monkeypatch.setattr(montage_plan_mod, "read_wav_mono", lambda _p: (np.zeros(1), 16000))
    monkeypatch.setattr(montage_plan_mod, "fine_energy", _energie_pic_a_6s)
    monkeypatch.setattr(montage_plan_mod, "measure_loudness", sfx_mod.measure_loudness)
    ffmpeg.codes = [1, 0]
    clip_id = _seed(tmp_path / "clipforge.db", montage="1")
    _rendre(tmp_path, clip_id)
    clip = _clip(tmp_path, clip_id)
    assert clip["status"] == "done", clip["error"]

    conn = dbmod.get_db(tmp_path / "clipforge.db")
    try:
        mots = [Word(start=r["start_s"], end=r["end_s"], text=r["text"]) for r in conn.execute(
            "SELECT start_s, end_s, text FROM words WHERE vod_id=? ORDER BY idx",
            (clip["vod_id"],))]
    finally:
        conn.close()
    assert mots, "le scenario doit porter des mots"
    mots_rel = relative_words(mots, clip["start_s"])
    teaser = choose_cold_open(mots_rel, _energie_pic_a_6s(None, None),
                              clip_dur_s=clip["end_s"] - clip["start_s"])
    assert teaser is not None
    assert len(ffmpeg.calls) == 2, ffmpeg.calls
    mesure, rendu = ffmpeg.calls
    assert mesure[-1] == "-" and rendu[-1].endswith(".mp4")

    for cmd in (mesure, rendu):
        graphe = ffmpeg.graphe(cmd)
        m = _AC0_RE.search(graphe)
        assert m, f"[ac0] n'est lu sur aucune entree dediee : {graphe}"
        args = _arguments_entree(cmd, int(m.group(1)))
        assert args[-1] == str(_source(tmp_path / "clipforge.db")), args
        recul = min(1.0, teaser.src_start)
        assert float(args[args.index("-ss") + 1]) == pytest.approx(
            5.0 + teaser.src_start - recul, abs=1e-6), args
        assert float(args[args.index("-t") + 1]) == pytest.approx(
            teaser.src_end - teaser.src_start + recul, abs=1e-6), args

    fc = ffmpeg.graphe(rendu)
    video = _VC0_RE.search(fc)
    assert video and video.group(1) == _AC0_RE.search(fc).group(1), fc
    args = _arguments_entree(rendu, int(video.group(1)))
    assert float(video.group(2)) == float(args[args.index("-t") + 1]), (video.group(0), args)
    effets = [a for a in rendu if a.endswith(".wav")]
    assert effets, "le scenario doit porter au moins un effet sonore"
    m = _EMOJI_RE.search(fc)
    assert m, fc
    emoji = _arguments_entree(rendu, int(m.group(1)))
    assert emoji[-1].endswith(".png"), emoji


def test_la_duree_attendue_du_v6_est_celle_de_la_timeline_de_sortie(
        tmp_path, ffmpeg, monkeypatch):
    """Revue finale V6 (lot C, C3) : la duree controlee apres rendu est celle de
    TimeMap (coupes retirees), pas celle du clip source. Une sortie qui dure
    tout le clip source (12 s) alors que le montage en retire le trou muet est
    un rendu faux."""
    monkeypatch.setattr(montage_plan_mod, "read_wav_mono", _synthetic_energy_wav)
    mots_rel = [Word(start=s, end=e, text=t) for s, e, t in _gap_words()]
    edl = build_edl(mots_rel, fine_energy(*_synthetic_energy_wav(Path("ignore"))),
                    clip_dur_s=12.0, cut_style="standard")
    sortie = TimeMap.from_edl(edl).output_duration
    assert sortie < 12.0 - 1.0, sortie

    db_path = tmp_path / "clipforge.db"
    clip_id = _seed_gap_scenario(db_path)
    conn = dbmod.get_db(db_path)
    try:
        clip = conn.execute("SELECT * FROM clips WHERE id=?", (clip_id,)).fetchone()
        job_dir = tmp_path / "jobs" / f"clip{clip_id}"
        job_dir.mkdir(parents=True)
        options = montage_plan_mod.build_montage_options(
            conn, clip, dbmod.get_vod(conn, clip["vod_id"]), job_dir)
    finally:
        conn.close()
    assert options["output_duration_s"] == sortie

    ffmpeg.durees = [12.0]
    _rendre(tmp_path, clip_id)
    assert _clip(tmp_path, clip_id)["status"] == "error"
    ffmpeg.durees = [sortie]
    ffmpeg.rendus = 0
    _rendre(tmp_path, clip_id)
    assert _clip(tmp_path, clip_id)["status"] == "done", _clip(tmp_path, clip_id)["error"]
