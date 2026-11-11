"""Compile l'EDL (teaser + segments) en filtres ffmpeg trim/concat.

Les segments du corps consomment directement le flux source [0:v]/[0:a] :
build_render_cmd seek deja sur le debut du clip (-ss start_s) et lit sa duree
entiere (-t dur), donc les temps de l'EDL (relatifs au debut du clip, cf.
montage/edl.py) tombent directement sur la timeline de cette entree, sans
decalage a appliquer ici.

Le teaser, joue en premier, lit sa PROPRE entree (teaser_input_args : le meme
fichier, seeke sur lui seul). Tire de [0:v]/[0:a], il forcait ffmpeg a decoder
toute la source jusqu'a lui avant que concat ne passe au corps, et chaque
image du corps decodee entre-temps restait en file (revue finale V6 : 10,6 Gio
pour un teaser a 42 s d'un clip 1080p60, rendu de 141,6 s sur une vraie VOD).

Video : chaque morceau est recale sur la grille de sortie, et fps revient
avant le split ; render.frame_grid dit comment et pourquoi.

split=2 en sortie video : la commande de rendu consomme le resultat deux fois
(branche cam, branche jeu), et un flux issu d'un filtre (par opposition a une
entree demuxee brute) ne peut alimenter qu'un seul consommateur sans split
explicite. L'audio n'a qu'un seul consommateur (le mix SFX) : pas de split.
"""
from fractions import Fraction
from pathlib import Path

from clipforge.render.frame_grid import RECUL_S, frame_grid, to_ms, video_piece
from clipforge.render.montage.edl import Edl, Segment

# Usage local : tolerance du raccourci « EDL plein » (_covers_whole_clip), qui
# compare les deux bords d'un segment unique a 0 et a clip_dur_s. Les deux sont
# arrondis a la ms par build_cuts et clip_dur_s par l'appelant : seule la
# derniere decimale peut differer d'un ULP. 1e-9 l'absorbe sans jamais prendre
# pour « plein » un segment qui laisserait fuir du silence de tete ou de queue.
_EPS = 1e-9


def teaser_input_args(source: Path, *, start_s: float, teaser: Segment) -> list[str]:
    """Arguments ffmpeg de l'entree dediee au teaser : la source, seekee sur le
    seul teaser (temps absolus = debut du clip + temps relatifs de l'EDL),
    _teaser_recul(teaser) plus tot (cf. frame_grid.video_piece).

    Seule source de verite de ces arguments : la mesure de loudness et le rendu
    consomment cette meme liste.
    """
    recul = _teaser_recul(teaser)
    return ["-ss", str(round(start_s + teaser.src_start - recul, 3)),
            "-t", str(_teaser_end(teaser)), "-i", str(source)]


def _teaser_duration(teaser: Segment) -> float:
    """Duree du teaser a la milliseconde."""
    return round(teaser.src_end - teaser.src_start, 3)


def _teaser_recul(teaser: Segment) -> float:
    """Lecture anticipee du teaser sur son entree : RECUL_S, jamais avant le
    debut du clip."""
    return round(min(RECUL_S, teaser.src_start), 3)


def _teaser_end(teaser: Segment) -> float:
    """Fin du teaser sur la timeline de son entree (0 = point de seek) : -t de
    l'entree ET fin de sa video."""
    return round(_teaser_recul(teaser) + _teaser_duration(teaser), 3)


def _covers_whole_clip(edl: Edl, clip_dur_s: float) -> bool:
    """Vrai seulement si l'EDL degenere en un unique segment couvrant [0, clip_dur].

    -ss/-t bornent deja l'entree ffmpeg au clip source, mais PAS au trim interne
    de l'EDL (finding 1, review Task 7) : un seul segment ne veut pas dire que ce
    segment couvre tout le clip -- ex. build_cuts peut tres bien produire un seul
    segment [2.88, 18.12] sur un clip de 20 s (tete/queue coupees, corps garde en
    un seul morceau). Verifier explicitement les deux bords, sinon le raccourci
    "" fait fuir du silence de tete/queue dans le rendu.
    """
    if edl.teaser is not None or len(edl.segments) != 1:
        return False
    seg = edl.segments[0]
    return seg.src_start <= _EPS and abs(seg.src_end - clip_dur_s) <= _EPS


def _check_teaser_index(edl: Edl, teaser_index: int | None) -> None:
    if edl.teaser is not None and (teaser_index is None or teaser_index < 1):
        raise ValueError(
            f"EDL avec teaser mais index d'entree teaser invalide ({teaser_index!r}) : "
            "le lire sur l'entree 0 ferait bufferiser tout le corps en memoire "
            "(cf. teaser_input_args)")


def video_cut(edl: Edl, *, clip_dur_s: float, fps: float,
              teaser_index: int | None = None, zoompan: bool = False) -> str:
    """Trim+concat video de l'EDL, "" seulement si l'EDL couvre deja tout le clip
    (un seul segment [0, clip_dur] sans teaser -- cf. _covers_whole_clip) et
    que la branche cam ne passe pas par zoompan.

    `fps` : cadence de la VOD. Chaque morceau et la sortie de concat sont remis
    sur frame_grid(fps), le texte que montage_plan donne aussi a zoompan (cf.
    render.frame_grid).

    `teaser_index` : index, dans la commande ffmpeg, de l'entree construite par
    teaser_input_args. Obligatoire et >= 1 si l'EDL porte un teaser (ValueError
    sinon), ignore sans teaser.

    `zoompan` : vrai quand build_render_cmd insere zoompan sur la branche cam
    (zoom_expr != "1"). zoompan re-date ses images sur la grille : sans coupe,
    la branche jeu gardait les instants de la source apres -ss et cam et jeu
    divergeaient (revue A2, fix round 1 : 420 images sur 422 a 29,97 ips). La
    video passe alors par la grille, en un seul morceau.

    La video du teaser est bornee par trim=end, absolu sur la timeline de son
    entree (0 = point de seek, _teaser_recul avant le teaser) : -t en option
    d'entree compte sa duree depuis la PREMIERE image decodee, et quand celle-ci
    tombe apres le point de seek la fenetre deborde d'autant et ramasse l'image
    qui suit la fin du teaser (revue du lot A, fix round 1 : 14 teasers sur 30 a
    30 ips). L'audio commence pile au point de seek : sa branche saute le recul
    (atrim, cf. audio_cut).

    Se termine par `[vcut]fps=...,split=2[vcuta][vcutb]` : build_render_cmd lit
    ses branches cam/jeu sur ces deux labels des que `video_cut` est non vide.
    """
    if _covers_whole_clip(edl, clip_dur_s) and not zoompan:
        return ""  # un seul morceau qui couvre tout le clip : -ss/-t de la commande suffit
    _check_teaser_index(edl, teaser_index)
    texte = frame_grid(fps)
    grille = Fraction(texte)
    morceaux = []
    if edl.teaser is not None:
        morceaux.append((f"[{teaser_index}:v]trim=end={_teaser_end(edl.teaser):.3f}",
                         _teaser_recul(edl.teaser), to_ms(_teaser_duration(edl.teaser))))
    for seg in edl.segments:
        tete = (f"[0:v]trim=start={max(0.0, seg.src_start - RECUL_S):.3f}"
                f":end={seg.src_end:.3f}")
        morceaux.append((tete, round(seg.src_start, 3),
                         to_ms(seg.src_end) - to_ms(seg.src_start)))
    filtres, debut_ms = [], 0
    for i, (tete, debut_source, duree_ms) in enumerate(morceaux):
        filtres.append(video_piece(tete, debut_source, debut_ms, duree_ms, grille, texte,
                                   f"vc{i}"))
        debut_ms += duree_ms
    entrees = "".join(f"[vc{i}]" for i in range(len(morceaux)))
    return ";".join([*filtres, f"{entrees}concat=n={len(morceaux)}:v=1:a=0[vcut]",
                     f"[vcut]fps={texte},split=2[vcuta][vcutb]"])


def audio_cut(edl: Edl, *, clip_dur_s: float, teaser_index: int | None = None,
              zoompan: bool = False) -> str:
    """Trim+concat audio de l'EDL, "" seulement si l'EDL couvre deja tout le clip
    (meme contrat que video_cut, `teaser_index` et `zoompan` compris). Sans
    coupe mais sous zoompan, l'audio passe tel quel ([0:a]anull[acut]) :
    build_render_cmd exige les deux graphes ensemble.

    Produit `[acut]` : render/montage_plan.py y branche le mix SFX
    (render.sfx.audio_filter avec src="[acut]") au lieu de la source brute
    [0:a].
    """
    if _covers_whole_clip(edl, clip_dur_s):
        return "[0:a]anull[acut]" if zoompan else ""
    _check_teaser_index(edl, teaser_index)
    branches = []
    if edl.teaser is not None:
        branches.append(f"[{teaser_index}:a]atrim=start={_teaser_recul(edl.teaser):.3f},"
                        "asetpts=PTS-STARTPTS")
    branches += [f"[0:a]atrim=start={seg.src_start:.3f}:end={seg.src_end:.3f},"
                 "asetpts=PTS-STARTPTS" for seg in edl.segments]
    filtres = [f"{branche}[ac{i}]" for i, branche in enumerate(branches)]
    entrees = "".join(f"[ac{i}]" for i in range(len(branches)))
    return ";".join([*filtres, f"{entrees}concat=n={len(branches)}:v=0:a=1[acut]"])
