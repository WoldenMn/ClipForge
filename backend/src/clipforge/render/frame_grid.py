"""Grille d'images de sortie du montage V6 : la cadence rationnelle que zoompan
et chaque filtre fps relisent a l'identique (frame_grid), et le morceau video qui
remplit exactement ses rangs (video_piece). render.filtergraph y compile l'EDL.

Chaque morceau (teaser puis segments) remplit exactement les rangs de la grille
de sortie (1/fps) qui tombent dans son intervalle de sortie, la ou l'audio et
TimeMap le placent (somme des durees EDL). Recolles bout a bout par concat, les
morceaux arrondis chacun a des images entieres decalaient les suivants (derive
en marche aleatoire, jusqu'a -62 ms mesures a 30 ips), et concat, qui date en
microsecondes tronquees, sortait des instants que le zoompan de la branche cam,
date sur sa propre grille 1/fps, ne retrouvait pas : vstack emettait deux images
au lieu d'une et la facecam saccadait apres presque chaque coupe (lot A2). fps
avant le split rend aux deux branches la base de temps de zoompan.
"""
import math
from fractions import Fraction

_BORNE_GRILLE = 1001000  # borne de zoompan (av_parse_video_rate), numerateur ET denominateur
RECUL_S = 1.0  # lecture anticipee de chaque morceau, cf. video_piece


def frame_grid(fps: float) -> str:
    """Grille d'images de la video du montage V6, en texte "num/den".

    zoompan lit sa cadence en <video_rate> et la ramene par av_d2q(fps, 1001000),
    numerateur et denominateur bornes ; le filtre fps evalue la sienne et la
    ramene par av_d2q(fps, INT_MAX). D'une meme cadence non ronde (59.99998924...,
    avg_frame_rate 334800000/5580001), les deux tiraient deux grilles (60/1 contre
    334800000/5580001) et vstack sortait deux images par instant (revue A2, fix
    round 1). Rend la grille de zoompan : av_d2q d'un flottant est l'av_reduce de
    sa valeur exacte. Ses deux termes tiennent sous la borne, donc zoompan et fps
    relisent ce texte sans l'arrondir (verifie sur ffmpeg 9.0.1, cf.
    test_montage_grid_integration.py).
    """
    exacte = Fraction(fps)
    grille = _av_reduce(exacte.numerator, exacte.denominator, _BORNE_GRILLE)
    return f"{grille.numerator}/{grille.denominator}"


def _av_reduce(num: int, den: int, borne: int) -> Fraction:
    """Port de libavutil av_reduce pour num, den > 0 : dernier convergent de la
    fraction continue de num/den dont les deux termes tiennent sous `borne`, ou
    la reduite intermediaire suivante si elle approche mieux."""
    pgcd = math.gcd(num, den)
    num, den = num // pgcd, den // pgcd
    if num <= borne and den <= borne:
        return Fraction(num, den)
    (p0, q0), (p1, q1) = (0, 1), (1, 0)
    while den:
        x = num // den
        p2, q2 = x * p1 + p0, x * q1 + q0
        if p2 > borne or q2 > borne:
            if p1:
                x = (borne - p0) // p1
            if q1:
                x = min(x, (borne - q0) // q1)
            if den * (2 * x * q1 + q0) > num * q1:
                p1, q1 = x * p1 + p0, x * q1 + q0
            break
        (p0, q0), (p1, q1) = (p1, q1), (p2, q2)
        num, den = den, num - den * x
    return Fraction(p1, q1)


def to_ms(t: float) -> int:
    """Instant en millisecondes entieres : le grain des debuts de morceau (A)."""
    return round(t * 1000)


def video_piece(tete: str, debut_source: float, debut_ms: int, duree_ms: int,
                grille: Fraction, texte: str, label: str) -> str:
    """Morceau video qui occupe les rangs [ceil(A*G), ceil((A+d)*G)) de la
    sortie, A = `debut_ms`, G = `grille` (`texte` : son ecriture frame_grid).

    Le rang k du morceau vise la source a D + k/G, D = debut_source +
    ceil(A*G)/G - A. Sa premiere image est posee sur le rang le plus proche de
    sa position ideale, round((STARTPTS - D)*G), et les suivantes gardent leur
    ecart a elle. Recalees chacune sur D (setpts=PTS-D), elles etaient arrondies
    une a une par fps : sur une source horodatee a la milliseconde (mkv, webm),
    quand D tombait a mi-rang, la gigue de 0,33 ms faisait basculer l'arrondi
    une image sur deux, doublon puis saut dans le morceau (revue A2, fix round
    1). Source a la cadence de la grille : chaque image tombe sur son rang.
    Contrat : au plus une image de sortie (1/G) d'ecart a la position ideale ;
    a la cadence de la grille, seulement aux bords du morceau, hors grille,
    aussi a l'interieur (voir le recul, plus bas). tpad clone la derniere image
    tant que fps en redemande et trim=end_frame coupe au compte (avec stop=1,
    19 morceaux sur 860 finissaient une image trop tot, grille 24 sur
    source 60).

    `tete` ouvre la lecture RECUL_S avant le segment (jamais avant le debut du
    clip). Un morceau tout entier dans un trou video de la source, l'audio
    continuant, ne recevait aucune image et ne sortait rien : concat recollait
    la suite plus tot, tout ce qui suivait jouait decale (revue A2, fix round
    2 : 600 images au lieu de 642, -700 ms jusqu'a la fin ; teaser vide, tout
    le clip decale de 1,4 s). Il tient maintenant la derniere image qui precede
    le trou, si elle tombe dans ce recul.

    Ce recul deplace l'ancrage : STARTPTS est celui de la premiere image de la
    LECTURE ANTICIPEE, et non plus celui de la premiere image de la fenetre ; c'est
    donc la phase de cette image-la que round() tranche pour tout le morceau.
    Quand la source ne tombe pas sur la grille (VFR, ou grille differente de la
    cadence source), la repartition interieure bascule d'un rang -- jamais plus
    d'une image (re-revue du lot A2 : sur une meme graine VFR, doublons
    interieurs passes de 0 a 8). Sur une source plus lente que la grille,
    l'image qui precede la fenetre peut occuper les deux premiers rangs.
    """
    premier = math.ceil(Fraction(debut_ms, 1000) * grille)
    apres = math.ceil(Fraction(debut_ms + duree_ms, 1000) * grille)
    decalage = debut_source + float(premier / grille - Fraction(debut_ms, 1000))
    num, den = grille.numerator, grille.denominator
    return (f"{tete},setpts=PTS-STARTPTS+round((STARTPTS*TB-{decalage:.6f})*{num}/{den})"
            f"*{den}/{num}/TB,tpad=stop_mode=clone:stop=-1,fps={texte}:start_time=0,"
            f"trim=end_frame={apres - premier}[{label}]")
