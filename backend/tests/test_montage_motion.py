import math
import re

import pytest

from clipforge.render.montage.motion import (
    SHAKE_OFFSETS_PX,
    PunchWindow,
    build_motion,
    legacy_zoom_expression,
    shake_offset_at,
    zoom_at,
)


def _eval_expr(expr: str, it: float) -> float:
    """Evalue une expression ffmpeg emise par ce module, au point `it`.

    Namespace restreint : implemente uniquement les fonctions ffmpeg utilisees
    par ce module (between/gte/lt/clip/mod/min). Remplace ffmpeg lui-meme
    dans les tests (CI sans ffmpeg) -- c'est le but de cet evaluateur, pas un
    sandbox Python general.
    """
    namespace = {
        "between": lambda x, a, b: 1.0 if a <= x <= b else 0.0,
        "gte": lambda a, b: 1.0 if a >= b else 0.0,
        "lt": lambda a, b: 1.0 if a < b else 0.0,
        "clip": lambda v, lo, hi: min(max(v, lo), hi),
        "mod": lambda a, b: math.fmod(a, b),
        "min": min,
        "it": it,
    }
    return eval(expr, {"__builtins__": {}}, namespace)


def test_eval_expr_valide_sur_connu_bon_et_connu_mauvais():
    """L'instrument se controle avant de servir a juger (regle CLAUDE.md).

    `between` ffmpeg est inclusif aux DEUX bornes. Sans verifier x==a et x==b,
    trois implementations fausses (borne haute exclusive, borne basse exclusive,
    les deux exclusives) passeraient inchangees : seuls les points interieur et
    loin-exterieur ne les distinguent pas.
    """
    assert _eval_expr("between(it,1.000,2.000)", 1.5) == 1.0
    assert _eval_expr("between(it,1.000,2.000)", 1.0) == 1.0     # borne basse incluse
    assert _eval_expr("between(it,1.000,2.000)", 2.0) == 1.0     # borne haute incluse
    assert _eval_expr("between(it,1.000,2.000)", 3.0) == 0.0
    assert _eval_expr("gte(it,2.000)", 2.0) == 1.0
    assert _eval_expr("gte(it,2.000)", 1.999) == 0.0
    assert _eval_expr("clip(it,0,1)", 1.5) == 1.0
    assert _eval_expr("clip(it,0,1)", -0.5) == 0.0
    assert _eval_expr("mod(it,2)", 3.0) == 1.0
    assert _eval_expr("mod(it,2)", 4.0) == 0.0
    assert _eval_expr("min(it,1.35)", 2.0) == 1.35
    assert _eval_expr("min(it,1.35)", 1.0) == 1.0


def test_zoom_at_monte_en_douceur_et_redescend():
    w = [PunchWindow(10.0, 11.4)]
    assert zoom_at(8.0, w, []) == pytest.approx(1.0)
    assert zoom_at(9.3, w, []) > 1.0                 # attaque de 0,8 s : partie a 9,2
    assert zoom_at(9.6, w, []) == pytest.approx(1.11)  # mi-attaque : smoothstep(0,5)
    assert zoom_at(10.5, w, []) == pytest.approx(1.22)
    assert 1.0 < zoom_at(11.7, w, []) < 1.22         # relache en cours
    assert zoom_at(12.5, w, []) == pytest.approx(1.0)


def test_continuite_pas_de_saut_hors_coupes():
    w = [PunchWindow(5.0, 6.4)]
    prev = zoom_at(0.0, w, [])
    for i in range(1, 1000):                         # pas de 10 ms sur 10 s
        cur = zoom_at(i / 100, w, [])
        assert abs(cur - prev) < 0.02                # < 2 % par 10 ms
        prev = cur


def test_base_alterne_a_chaque_coupe_et_borne_tenue():
    cuts = [2.0, 5.0, 9.0]
    assert zoom_at(1.0, [], cuts) == pytest.approx(1.0)
    assert zoom_at(3.0, [], cuts) == pytest.approx(1.035)
    assert zoom_at(6.0, [], cuts) == pytest.approx(1.0)
    assert zoom_at(9.5, [], cuts) == pytest.approx(1.035)
    empile = [PunchWindow(2.5, 3.9)]                 # pic sur base haute
    assert zoom_at(3.0, empile, cuts) <= 1.35


def test_expression_de_200_coupes_reste_sous_la_profondeur_d_ffmpeg():
    """Revue A2, fix round 1 (rendu a 200 segments) : l'evaluateur d'expressions
    d'ffmpeg 9.0.1 refuse une somme a plat de plus de 96 termes, et zoompan
    echouait a se configurer ("Failed to configure output pad") des la 97e
    coupe. Les termes vont par paquets de 32 entre parentheses ; la valeur ne
    change pas."""
    coupes = [round(0.72 + 0.64 * k, 3) for k in range(200)]
    m = build_motion([], coupes, shake_at=[], fps=5.0)
    assert re.search(r"(?:gte\(it,[\d.]+\)\+){32}", m.zoom_expr) is None, m.zoom_expr[:400]
    for t in (0.0, 0.72, 1.0, 64.5, 127.44, 200.0):
        assert _eval_expr(m.zoom_expr, t) == pytest.approx(zoom_at(t, [], coupes), abs=1e-9)


def test_expression_sans_rien_reste_1():
    m = build_motion([], [], shake_at=[], fps=60.0)
    assert m.zoom_expr == "1"
    assert m.x_expr == "iw/2-(iw/zoom/2)"


def test_expression_compilee_contient_les_morceaux():
    m = build_motion([PunchWindow(3.0, 4.4)], [2.0], shake_at=[3.2], fps=60.0)
    assert "min(" in m.zoom_expr and "1.35" in m.zoom_expr
    assert "gte(it,2.0" in m.zoom_expr.replace(" ", "")
    assert "between(it,3.2" in m.x_expr.replace(" ", "")


def test_legacy_identique_a_v5():
    w = [PunchWindow(1.0, 2.4), PunchWindow(8.0, 9.4)]
    assert legacy_zoom_expression(w) == (
        "1+0.22*(between(it,1.000,2.400)+between(it,8.000,9.400))")


def test_precision_zoom_chaine_et_miroir_concordent():
    """zoom=1.2237 : _window_expr formate delta en .3f, zoom_at gardait le brut.

    Sur le plateau de la fenetre (attaque=1, relache=0), le miroir et la
    chaine compilee doivent tomber sur EXACTEMENT la meme valeur.
    """
    w = [PunchWindow(10.0, 11.4)]
    m = build_motion(w, [], shake_at=[], fps=60.0, zoom=1.2237)
    valeur_chaine = _eval_expr(m.zoom_expr, 10.5)
    valeur_miroir = zoom_at(10.5, w, [], zoom=1.2237)
    assert valeur_chaine == pytest.approx(valeur_miroir, abs=1e-6)


def test_precision_coupes_chaine_et_miroir_concordent():
    """cut_times=[2.00051, ...] : _base_expr formate en .3f, _base comparait le brut.

    A t=2.0007, le brut fait basculer la parite (2.0007 >= 2.00051) mais la
    chaine compilee ("gte(it,2.001)") ne bascule qu'a partir de 2.001.
    """
    cuts = [2.00051, 5.0, 9.0]
    m = build_motion([], cuts, shake_at=[], fps=60.0)
    t = 2.0007
    valeur_chaine = _eval_expr(m.zoom_expr, t)
    valeur_miroir = zoom_at(t, [], cuts)
    assert valeur_chaine == pytest.approx(valeur_miroir, abs=1e-6)


def test_miroir_zoom_expression_suit_zoom_at():
    configs = [
        {"windows": [PunchWindow(2.0, 3.4)], "cuts": [], "shake_at": []},
        {"windows": [], "cuts": [1.5, 4.0, 7.5], "shake_at": []},
        {
            "windows": [PunchWindow(2.0, 3.4), PunchWindow(9.0, 10.4)],
            "cuts": [1.5, 4.0, 7.5],
            "shake_at": [3.2, 9.5],
        },
    ]
    grille = [round(i * 0.06, 3) for i in range(210)]           # 0 .. 12.54s, 210 points
    for cfg in configs:
        m = build_motion(cfg["windows"], cfg["cuts"], shake_at=cfg["shake_at"], fps=60.0)
        for t in grille:
            valeur_chaine = _eval_expr(m.zoom_expr, t)
            valeur_miroir = zoom_at(t, cfg["windows"], cfg["cuts"])
            assert abs(valeur_chaine - valeur_miroir) < 1e-6, (cfg, t)


def test_miroir_shake_x_et_y():
    shake_at = [1.0, 4.5]
    fps = 60.0
    m = build_motion([], [], shake_at=shake_at, fps=fps)
    x_shake_expr = m.x_expr.replace("iw/2-(iw/zoom/2)", "0")
    y_shake_expr = m.y_expr.replace("ih*0.38-(ih/zoom*0.38)", "0")

    bornes = set()
    for ts in shake_at:
        for k in range(len(SHAKE_OFFSETS_PX) + 1):
            bornes.add(round(ts + k / fps, 3))
    grille = sorted(bornes | {round(i * 0.025, 3) for i in range(240)})  # 0 .. 5.975s + bornes

    for t in grille:
        dx, dy = shake_offset_at(t, shake_at, fps)
        assert _eval_expr(x_shake_expr, t) == pytest.approx(dx, abs=1e-6)
        assert _eval_expr(y_shake_expr, t) == pytest.approx(dy, abs=1e-6)

    # Miroir et expression partagent SHAKE_OFFSETS_PX : une permutation passait
    # des deux cotes. Le motif, image par image, est donc fixe ici, sa longueur
    # comprise : rien a la quatrieme image.
    assert [shake_offset_at(1.0 + (k + 0.5) / fps, [1.0], fps) for k in range(4)] == [
        (3, -2), (-2, 3), (1, -1), (0, 0)]
