"""Mouvement organique : ondulations douces de zoom et secousses de camera.

Reexporte PunchWindow/detect_punches/legacy_zoom_expression de motion_detect
pour garder un point d'import unique cote consommateurs (montage_plan.py les
trois, sfx.py la seule PunchWindow) :
la detection de pics audio et le repli V5 n'ont pas de logique propre a
l'engin organique, mais partagent son vocabulaire (fenetres de punch-in).

Toutes les expressions produites utilisent `it` (temps de sortie en secondes
dans zoompan), jamais `t` (temps source) : voir ffmpeg_cmd.py pour le point
de montage cam/game.

Arrondi partage (3 decimales) : toute quantite qui finit formatee en `:.3f`
dans une expression compilee (delta de zoom, cut_times, bornes de fenetre,
bornes de secousse) est arrondie a 3 decimales UNE SEULE FOIS avant d'etre
consommee par les deux chemins (miroir Python et compilateur d'expression).
Sinon le miroir compare une valeur que la chaine ffmpeg n'a jamais vue
(desync de precision, trouve en review de Task 5).
"""
from dataclasses import dataclass

from clipforge.render.montage.motion_detect import (
    PunchWindow,
    detect_punches,  # noqa: F401 -- reexporte pour montage_plan.py
    legacy_zoom_expression,  # noqa: F401 -- reexporte, repli V5 de montage_plan.py
)

ATTACK_S = 0.8
RELEASE_S = 0.6
CUT_STEP = 0.035
_PAQUET_COUPES = 32
ZOOM_MAX = 1.35
SHAKE_OFFSETS_PX = [(3, -2), (-2, 3), (1, -1)]
# On zoome vers le visage, pas le menton : le centre du recadrage reste a 38 %
# de la hauteur plutot qu'a la moitie. Source unique du biais (Task 7) :
# ffmpeg_cmd.py importe X_EXPR_DEFAULT/Y_EXPR_DEFAULT au lieu de le dupliquer.
_Y_BIAS = 0.38
X_EXPR_DEFAULT = "iw/2-(iw/zoom/2)"
Y_EXPR_DEFAULT = f"ih*{_Y_BIAS}-(ih/zoom*{_Y_BIAS})"


@dataclass(frozen=True)
class Motion:
    zoom_expr: str
    x_expr: str
    y_expr: str


def _r3(x: float) -> float:
    """Arrondi partage a 3 decimales -- meme grain que le formatage `:.3f`."""
    return round(x, 3)


def _smoothstep(u: float) -> float:
    p = min(max(u, 0.0), 1.0)
    return p * p * (3 - 2 * p)


def _window_contribution(t: float, w: PunchWindow, delta: float) -> float:
    attaque = _smoothstep((t - (w.start - ATTACK_S)) / ATTACK_S)
    relache = _smoothstep((t - w.end) / RELEASE_S)
    return delta * (attaque - relache)


def _base(t: float, cut_times: list[float]) -> float:
    parite = sum(1 for c in cut_times if t >= c) % 2
    return 1.0 + CUT_STEP * parite


def zoom_at(t: float, windows: list[PunchWindow], cut_times: list[float], *,
           zoom: float = 1.22) -> float:
    """Miroir Python de zoom_expr, pour les tests de propriete (continuite, bornes).

    total(t) = base(t) + somme des contributions de fenetre, plafonne a ZOOM_MAX.
    Arrondit delta/cut_times/bornes de fenetre comme build_motion (voir
    docstring module) pour rester un miroir fidele de la chaine compilee.
    """
    delta = _r3(zoom - 1.0)
    cuts = [_r3(c) for c in cut_times]
    fenetres = [PunchWindow(_r3(w.start), _r3(w.end)) for w in windows]
    total = _base(t, cuts) + sum(_window_contribution(t, w, delta) for w in fenetres)
    return min(total, ZOOM_MAX)


def shake_offset_at(t: float, shake_at: list[float], fps: float) -> tuple[float, float]:
    """Miroir Python des termes de secousse (x_expr/y_expr), pour les tests.

    Retourne (dx, dy) : somme des offsets SHAKE_OFFSETS_PX actifs a l'instant t.
    Arrondit les bornes de frame comme _shake_expr (voir docstring module).
    """
    dx = dy = 0.0
    for ts in shake_at:
        for k, (ox, oy) in enumerate(SHAKE_OFFSETS_PX):
            debut = _r3(ts + k / fps)
            fin = _r3(ts + (k + 1) / fps)
            if debut <= t <= fin:
                dx += ox
                dy += oy
    return dx, dy


def _smooth_expr(u: str) -> str:
    return f"(clip({u},0,1)*clip({u},0,1)*(3-2*clip({u},0,1)))"


def _window_expr(w: PunchWindow, delta: float) -> str:
    attaque = f"(it-{w.start - ATTACK_S:.3f})/{ATTACK_S:.3f}"
    relache = f"(it-{w.end:.3f})/{RELEASE_S:.3f}"
    return f"{delta:.3f}*({_smooth_expr(attaque)}-{_smooth_expr(relache)})"


def _base_expr(cut_times: list[float]) -> str:
    """Parite du nombre de coupes passees. L'evaluateur d'ffmpeg borne la
    profondeur d'une expression (MAX_DEPTH 100, libavutil/eval.c) et une somme y
    descend d'un niveau par terme : a plat, zoompan ne se configurait plus des
    la 97e coupe. Par paquets de _PAQUET_COUPES entre parentheses, la profondeur
    vaut a peu pres coupes/32 + 32. Mesure sur ffmpeg 9.0.1 : 2080 coupes
    passent sans fenetre de punch-in, 2081 echouent ; avec trois fenetres (un
    niveau chacune), 1984 passent, 1985 echouent. Aucun refus n'est code ici.

    Le temps de rendu croit plus vite que le nombre de coupes, et la pente
    s'accentue sur la plage mesuree (revue A2, 1080p60 NVENC : 97 coupes
    21,4 s, 150 coupes 52,7 s, 300 coupes 350,2 s, soit un exposant local de
    2,1 de 97 a 150 puis de 2,7 de 150 a 300). Rien n'est mesure au-dela de
    300 coupes ; a 300, le seul essai NVENC consomme deja plus de la moitie de
    RENDER_TIMEOUT_S (queue.py, 600 s), que le repli CPU paierait une seconde
    fois."""
    if not cut_times:
        return "1"
    termes = [f"gte(it,{c:.3f})" for c in sorted(cut_times)]
    somme = "+".join(f"({'+'.join(termes[i:i + _PAQUET_COUPES])})"
                     for i in range(0, len(termes), _PAQUET_COUPES))
    return f"1+{CUT_STEP:.3f}*mod({somme},2)"


def _shake_expr(shake_at: list[float], fps: float, axis: int) -> str:
    """axis 0 -> dx (x_expr), axis 1 -> dy (y_expr) ; memes fenetres sur les deux."""
    termes = []
    for ts in shake_at:
        for k, offset in enumerate(SHAKE_OFFSETS_PX):
            debut = _r3(ts + k / fps)
            fin = _r3(ts + (k + 1) / fps)
            termes.append(f"+between(it,{debut:.3f},{fin:.3f})*{offset[axis]}")
    return "".join(termes)


def build_motion(windows: list[PunchWindow], cut_times: list[float], *,
                 shake_at: list[float], fps: float, zoom: float = 1.22) -> Motion:
    """Compile les expressions zoompan (zoom/x/y) du mouvement organique.

    Sans fenetre ni coupe, `zoom_expr` reste "1" (chemin V5 sans zoompan
    preserve : ffmpeg_cmd n'insere le filtre que si `zoom_expr != "1"`).
    Arrondit delta/cut_times/bornes de fenetre comme zoom_at (voir docstring
    module) pour rester un miroir fidele.
    """
    delta = _r3(zoom - 1.0)
    cuts = [_r3(c) for c in cut_times]
    fenetres = [PunchWindow(_r3(w.start), _r3(w.end)) for w in windows]
    somme = _base_expr(cuts) + "".join(f"+{_window_expr(w, delta)}" for w in fenetres)
    zoom_expr = "1" if not fenetres and not cuts else f"min({somme},{ZOOM_MAX})"
    x_expr = X_EXPR_DEFAULT + _shake_expr(shake_at, fps, 0)
    y_expr = Y_EXPR_DEFAULT + _shake_expr(shake_at, fps, 1)
    return Motion(zoom_expr, x_expr, y_expr)
