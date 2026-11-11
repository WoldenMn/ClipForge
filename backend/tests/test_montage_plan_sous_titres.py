"""L'orchestrateur de montage, sous-titres V6 face aux coupes :
aucune ligne pop ne tient a cheval sur une coupe, du corps ou du teaser.

Decoupe de test_montage_plan.py (revue finale V6, lot D), dont viennent les
scenarios partages. ffmpeg entierement simule (fixture `ffmpeg` de conftest.py).
"""
import re
from pathlib import Path

import numpy as np
from test_montage_plan import _ass_times_s, _gap_words, _parole_avec_eclat, _seed_gap_scenario
from test_render_queue import _ass, _clip, _rendre

from clipforge.pipeline.segmenter import Word
from clipforge.render import montage_plan as montage_plan_mod
from clipforge.render.montage.edl import build_edl
from clipforge.render.montage.energy import fine_energy
from clipforge.render.montage.timemap import TimeMap

_POP_RE = re.compile(r"^Dialogue: 0,(\d+:\d{2}:\d{2}\.\d{2}),(\d+:\d{2}:\d{2}\.\d{2}),Pop,",
                     re.MULTILINE)


def test_aucune_ligne_pop_du_v6_ne_chevauche_une_coupe(tmp_path, ffmpeg, monkeypatch):
    """Revue finale V6 (lot C, C1) : sur la vraie VOD, 8 lignes Pop tenaient a
    cheval sur une coupe (« TOUCHE ! NON » sur la coupe teaser -> corps). La
    coupe retire la pause qui separait les deux phrases, et group_words les
    fusionnait. Scenario : un teaser (eclat sous mot10) et une coupe de corps
    (le trou muet). Coupes recalculees a part, avec les memes primitives, et
    comparees au centieme, la resolution de l'ASS ecrit. Ici seule la coupe du
    corps soude deux groupes (les trois mots du teaser remplissent le leur) :
    l'espion sur build_pop_ass tient aussi la coupe du teaser."""
    lire = _parole_avec_eclat(7.15, 7.4)
    appels: list[dict] = []
    reel_build_pop_ass = montage_plan_mod.build_pop_ass

    def _build_pop_ass_espion(*args, **kwargs):
        appels.append(kwargs)
        return reel_build_pop_ass(*args, **kwargs)

    monkeypatch.setattr(montage_plan_mod, "read_wav_mono", lire)
    monkeypatch.setattr(montage_plan_mod, "build_pop_ass", _build_pop_ass_espion)
    mots_rel = [Word(start=s, end=e, text=t) for s, e, t in _gap_words()]
    edl = build_edl(mots_rel, fine_energy(*lire(Path("ignore"))), clip_dur_s=12.0,
                    cut_style="standard")
    coupes = TimeMap.from_edl(edl).cut_times()
    assert edl.teaser is not None and len(edl.segments) >= 2 and len(coupes) >= 2, (edl, coupes)

    clip_id = _seed_gap_scenario(tmp_path / "clipforge.db")
    _rendre(tmp_path, clip_id)
    assert _clip(tmp_path, clip_id)["status"] == "done", _clip(tmp_path, clip_id)["error"]

    lignes = [(round(a * 100), round(b * 100)) for a, b in
              ((_ass_times_s(d)[0], _ass_times_s(f)[0])
               for d, f in _POP_RE.findall(_ass(tmp_path, clip_id)))]
    assert len(lignes) >= 5, lignes
    a_cheval = [(d, f, c) for d, f in lignes for c in coupes if d < round(c * 100) < f]
    assert a_cheval == [], (a_cheval, coupes)
    assert len(appels) == 1 and appels[0].get("cut_times") == coupes, (appels, coupes)


def _mots_teaser_colle() -> list[tuple[float, float, str]]:
    """19 mots dont UN long (6,6 a 8,2) : le teaser tient dans ce seul mot, son
    groupe pop n'est donc pas plein quand la coupe arrive. Aucun ecart
    au-dessus du seuil "standard" (0,55 s) : la coupe du teaser est la SEULE."""
    mots = [(0.10, 0.50, "on")]
    t = 1.0
    while t + 0.4 <= 6.4:
        mots.append((round(t, 3), round(t + 0.4, 3), f"mot{len(mots)}"))
        t += 0.7
    mots.append((6.6, 8.2, "OH"))
    t = 8.6
    while t + 0.4 <= 11.7:
        mots.append((round(t, 3), round(t + 0.4, 3), f"mot{len(mots)}"))
        t += 0.7
    return mots


def _parole_pleine_avec_eclat(debut: float, fin: float):
    """read_wav_mono de substitution : 12 s de parole continue (aucun trou muet)
    et un eclat sur [debut, fin) -- de quoi faire un teaser, pas une coupe."""
    def _lire(path: Path) -> tuple[np.ndarray, int]:
        sr, dur = 16000, 12.0
        t = np.arange(int(dur * sr)) / sr
        x = 0.3 * np.sin(2 * np.pi * 300 * t)
        fort = (t >= debut) & (t < fin)
        x[fort] = 0.95 * np.sin(2 * np.pi * 300 * t[fort])
        return x.astype(np.float32), sr
    return _lire


def test_aucune_ligne_pop_ne_chevauche_la_coupe_teaser_vers_corps(
        tmp_path, ffmpeg, monkeypatch):
    """Revue finale V6 (fix round 1, R8) : sur la vraie VOD, « TOUCHE ! NON »
    tenait a cheval sur la coupe teaser -> corps. Le test de C1 ne le prouvait
    pas : ses trois mots de teaser remplissaient leur groupe, seule la coupe du
    CORPS y soudait deux groupes. Ici le teaser tient dans un seul mot long, la
    coupe du teaser est la seule coupe, et sans cut_times la derniere ligne du
    teaser se soude au premier mot du corps (ecart de sortie 0,18 s, sous les
    0,35 s de max_gap_s)."""
    lire = _parole_pleine_avec_eclat(7.0, 7.8)
    monkeypatch.setattr(montage_plan_mod, "read_wav_mono", lire)
    mots_rel = [Word(start=s, end=e, text=t) for s, e, t in _mots_teaser_colle()]
    edl = build_edl(mots_rel, fine_energy(*lire(Path("ignore"))), clip_dur_s=12.0,
                    cut_style="standard")
    coupes = TimeMap.from_edl(edl).cut_times()
    assert edl.teaser is not None and len(edl.segments) == 1, edl
    assert coupes == [round(edl.teaser.src_end - edl.teaser.src_start, 3)], (coupes, edl.teaser)

    clip_id = _seed_gap_scenario(tmp_path / "clipforge.db", mots=_mots_teaser_colle())
    _rendre(tmp_path, clip_id)
    assert _clip(tmp_path, clip_id)["status"] == "done", _clip(tmp_path, clip_id)["error"]

    lignes = [(round(a * 100), round(b * 100)) for a, b in
              ((_ass_times_s(d)[0], _ass_times_s(f)[0])
               for d, f in _POP_RE.findall(_ass(tmp_path, clip_id)))]
    assert len(lignes) >= 5, lignes
    a_cheval = [(d, f, c) for d, f in lignes for c in coupes if d < round(c * 100) < f]
    assert a_cheval == [], (a_cheval, coupes)
