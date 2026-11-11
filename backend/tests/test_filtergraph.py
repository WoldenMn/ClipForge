import math
import random
import re
from fractions import Fraction
from pathlib import Path

import pytest

from clipforge.render.filtergraph import audio_cut, teaser_input_args, video_cut
from clipforge.render.frame_grid import frame_grid
from clipforge.render.montage.edl import Edl, Segment
from clipforge.render.montage.timemap import TimeMap

TEMPLATE_EDL = Edl(
    teaser=Segment(9.5, 11.0),
    segments=[Segment(0.0, 4.12), Segment(6.0, 9.4)],
)
TEASER_INDEX = 3
FPS = 30.0

# Sortie : teaser [0, 1.5), corps 1 [1.5, 5.62), corps 2 [5.62, 9.02). A 30 ips,
# premieres images de chaque morceau aux rangs ceil(A*30) = 0, 45, 169, 271 :
# 45, 124 et 102 images. Le corps 2 commence a 5.62 s mais sa premiere image
# sort a 169/30 = 5.6333 s : elle doit montrer la source a 6.0 + 0.013333 s.
# Chaque morceau lit sa source une seconde avant (trim du corps, entree du
# teaser seekee plus tot, d'ou D = 1 pour le teaser), jamais avant le clip.
VIDEO_GOLDEN = (
    "[3:v]trim=end=2.500,setpts=PTS-STARTPTS+round((STARTPTS*TB-1.000000)*30/1)*1/30/TB,"
    "tpad=stop_mode=clone:stop=-1,fps=30/1:start_time=0,trim=end_frame=45[vc0];"
    "[0:v]trim=start=0.000:end=4.120,setpts=PTS-STARTPTS+round((STARTPTS*TB-0.000000)*30/1)*1/30/TB,"
    "tpad=stop_mode=clone:stop=-1,fps=30/1:start_time=0,trim=end_frame=124[vc1];"
    "[0:v]trim=start=5.000:end=9.400,setpts=PTS-STARTPTS+round((STARTPTS*TB-6.013333)*30/1)*1/30/TB,"
    "tpad=stop_mode=clone:stop=-1,fps=30/1:start_time=0,trim=end_frame=102[vc2];"
    "[vc0][vc1][vc2]concat=n=3:v=1:a=0[vcut];[vcut]fps=30/1,split=2[vcuta][vcutb]"
)

AUDIO_GOLDEN = (
    "[3:a]atrim=start=1.000,asetpts=PTS-STARTPTS[ac0];"
    "[0:a]atrim=start=0.000:end=4.120,asetpts=PTS-STARTPTS[ac1];"
    "[0:a]atrim=start=6.000:end=9.400,asetpts=PTS-STARTPTS[ac2];"
    "[ac0][ac1][ac2]concat=n=3:v=0:a=1[acut]"
)

BODY_ONLY_EDL = Edl(None, [Segment(0.0, 4.12), Segment(6.0, 9.4)])

_MORCEAU_RE = re.compile(r"setpts=PTS-STARTPTS\+round\(\(STARTPTS\*TB-([\d.]+)\)\*(\d+)/(\d+)\)"
                         r"\*(\d+)/(\d+)/TB,tpad=stop_mode=clone:stop=-1,"
                         r"fps=(\d+/\d+):start_time=0,trim=end_frame=(\d+)\[vc(\d+)\]")


@pytest.mark.parametrize("fps, grille", [
    (60.0, "60/1"), (24.0, "24/1"), (29.97002997002997, "30000/1001"),
    (59.94005994005994, "60000/1001"), (59.999989247313756, "60/1"), (29.99999, "30/1"),
    (59.99500013888503, "11999/200"), (59.99180275973755, "944091/15737")])
def test_frame_grid_is_the_rate_zoompan_derives_with_both_terms_bounded(fps, grille):
    """Attendus lus sur ffmpeg 9.0.1 (showinfo derriere zoompan, cf.
    test_montage_grid_integration.py) : 59.99998924... donne 60/1 a zoompan,
    334800000/5580001 au filtre fps ; Fraction.limit_denominator, qui ne borne
    que le denominateur, en tirait une troisieme (5579999/93000)."""
    assert frame_grid(fps) == grille


def test_video_cut_golden_string_teaser_first_from_its_own_input():
    assert video_cut(TEMPLATE_EDL, clip_dur_s=11.0, fps=FPS,
                     teaser_index=TEASER_INDEX) == VIDEO_GOLDEN


def test_audio_cut_golden_string_teaser_first_from_its_own_input():
    assert audio_cut(TEMPLATE_EDL, clip_dur_s=11.0, teaser_index=TEASER_INDEX) == AUDIO_GOLDEN


def test_video_cut_golden_string_without_teaser_at_a_fractional_rate():
    """29.97 ips : la grille est 30000/1001, pas 29.97/1 ; la premiere image du
    corps 2 (sortie a 1.5 s) sort au rang 45, a 1.5015 s."""
    edl = Edl(None, [Segment(0.5, 2.0), Segment(3.0, 4.5)])
    assert video_cut(edl, clip_dur_s=6.0, fps=29.97002997002997) == (
        "[0:v]trim=start=0.000:end=2.000,setpts=PTS-STARTPTS+round((STARTPTS*TB-0.500000)*30000/1001)*1001/30000/TB,"
        "tpad=stop_mode=clone:stop=-1,fps=30000/1001:start_time=0,trim=end_frame=45[vc0];"
        "[0:v]trim=start=2.000:end=4.500,setpts=PTS-STARTPTS+round((STARTPTS*TB-3.001500)*30000/1001)*1001/30000/TB,"
        "tpad=stop_mode=clone:stop=-1,fps=30000/1001:start_time=0,trim=end_frame=45[vc1];"
        "[vc0][vc1]concat=n=2:v=1:a=0[vcut];[vcut]fps=30000/1001,split=2[vcuta][vcutb]")


@pytest.mark.parametrize("fps, texte", [(30.0, "30/1"), (60.0, "60/1"),
                                        (29.97002997002997, "30000/1001"), (24.0, "24/1")])
def test_every_cut_lands_on_the_first_frame_slot_after_its_audio_time(fps, texte):
    """Revue du lot A2 : concat empile les images de chaque morceau bout a bout,
    en microsecondes tronquees ; la video derivait d'une coupe a l'autre (une
    marche aleatoire, jusqu'a -62 ms mesures) et zoompan, qui date ses images
    sur sa propre grille, ne tombait plus sur les memes instants que la branche
    jeu : la facecam saccadait apres chaque coupe. Ici chaque morceau remplit
    exactement les rangs de la grille qui tombent dans son intervalle audio,
    tel que TimeMap le place (debuts de morceau et coupes), donc l'ecart
    video/audio reste sous une image, sans cumul, quel que soit le nombre de
    coupes. Tous les fps= portent le meme texte num/den (revue A2, fix round 1)."""
    grille = Fraction(texte)
    rng = random.Random(fps)
    for _ in range(50):
        bornes = sorted(round(rng.uniform(0.1, 59.9), 3) for _ in range(10))
        corps = [Segment(bornes[i], bornes[i + 1]) for i in range(0, 10, 2)]
        a = round(rng.uniform(3.0, 55.0), 3)
        edl = Edl(Segment(a, round(a + rng.uniform(1.2, 1.8), 3)), corps)
        graphe = video_cut(edl, clip_dur_s=60.0, fps=fps, teaser_index=4)
        morceaux = _MORCEAU_RE.findall(graphe)
        assert [int(m[7]) for m in morceaux] == list(range(6)), graphe
        assert {(f"{m[1]}/{m[2]}", f"{m[4]}/{m[3]}", m[5]) for m in morceaux} == {(texte,) * 3}
        assert f"[vcut]fps={texte},split=2" in graphe
        tmap = TimeMap.from_edl(edl)
        debuts = [0.0, *(tmap.to_output(seg.src_start) for seg in corps), tmap.output_duration]
        assert set(tmap.cut_times()) <= set(debuts), (tmap.cut_times(), debuts)
        debuts_ms = [Fraction(round(d * 1000), 1000) for d in debuts]
        rangs = [math.ceil(d * grille) for d in debuts_ms]
        rang = 0
        for i, (seg, m) in enumerate(zip([edl.teaser, *corps], morceaux)):
            decalage, images = m[0], m[6]
            assert rang == rangs[i], (graphe, debuts)
            source = min(1.0, seg.src_start) if i == 0 else seg.src_start
            attendu = source + float(rangs[i] / grille - debuts_ms[i])
            assert float(decalage) == pytest.approx(attendu, abs=1e-6), graphe
            rang += int(images)
        assert rang == rangs[-1], graphe


@pytest.mark.parametrize("cut, flux, kwargs", [(video_cut, "[0:v]", {"fps": FPS}),
                                               (audio_cut, "[0:a]", {})])
def test_no_trim_reads_the_teaser_on_the_body_input(cut, flux, kwargs):
    """Revue finale V6 : un teaser tire de [0:v]/[0:a] force ffmpeg a decoder
    toute la source jusqu'a lui avant que concat ne passe au corps, et chaque
    image du corps decodee entre-temps reste en file (10,6 Gio mesures pour un
    teaser a 42 s d'un clip 1080p60). L'entree 0 ne sert plus que le corps."""
    graphe = cut(TEMPLATE_EDL, clip_dur_s=11.0, teaser_index=TEASER_INDEX, **kwargs)
    assert graphe.count(flux) == len(TEMPLATE_EDL.segments)
    assert "9.500" not in graphe and "11.000" not in graphe


@pytest.mark.parametrize("index", [None, 0, -1])
@pytest.mark.parametrize("cut, kwargs", [(video_cut, {"fps": FPS}), (audio_cut, {})])
def test_teaser_without_a_valid_input_index_raises(cut, kwargs, index):
    """0 (ou moins) designerait l'entree du corps : tout le clip jouerait en
    teaser et le chemin qui bufferise reviendrait."""
    with pytest.raises(ValueError, match="teaser"):
        cut(TEMPLATE_EDL, clip_dur_s=11.0, teaser_index=index, **kwargs)


@pytest.mark.parametrize("teaser, recul, ss", [(Segment(9.5, 11.0), 1.0, "13.5"),
                                               (Segment(0.3, 1.8), 0.3, "5.0")])
def test_every_piece_reads_its_source_one_second_early_never_before_the_clip(teaser, recul, ss):
    """Revue A2, fix round 2 : un morceau tout entier dans un trou video (audio
    continu) ne recevait aucune image et ne sortait rien ; concat recollait la
    suite plus tot et tout ce qui suivait jouait decale (-700 ms mesures jusqu'a
    la fin du clip ; teaser vide : tout le clip decale). Le corps ouvre son trim
    une seconde avant son segment, le teaser seeke son entree une seconde plus
    tot, jamais avant le debut du clip ; l'instant vise (D, setpts) ne bouge pas
    et l'audio du teaser saute ce recul."""
    edl = Edl(teaser, [Segment(0.4, 4.12), Segment(6.0, 9.4)])
    args = teaser_input_args(Path("s.mp4"), start_s=5.0, teaser=teaser)
    assert args[:4] == ["-ss", ss, "-t", str(round(1.5 + recul, 3))]
    video = video_cut(edl, clip_dur_s=12.0, fps=FPS, teaser_index=2)
    assert video.startswith(f"[2:v]trim=end={1.5 + recul:.3f},"
                            f"setpts=PTS-STARTPTS+round((STARTPTS*TB-{recul:.6f})*30/1)")
    assert "[0:v]trim=start=0.000:end=4.120,setpts=PTS-STARTPTS+round((STARTPTS*TB-0.4" in video
    assert "[0:v]trim=start=5.000:end=9.400,setpts=PTS-STARTPTS+round((STARTPTS*TB-6.0" in video
    assert audio_cut(edl, clip_dur_s=12.0, teaser_index=2).startswith(
        f"[2:a]atrim=start={recul:.3f},asetpts=PTS-STARTPTS[ac0];"
        "[0:a]atrim=start=0.400:end=4.120,")


def test_teaser_video_ends_where_its_input_duration_says():
    """-t en option d'ENTREE devient une duree comptee depuis la PREMIERE image
    decodee, pas depuis le point de seek : si elle tombe d ms apres, la video
    deborde de d et ramasse une image de trop (revue du lot A : 14 teasers sur
    30 a 30 ips). trim=end, absolu sur la timeline de l'entree (0 = point de
    seek, une seconde avant le teaser), borne la video a la MEME fin que -t.
    L'audio commence pile au point de seek : sa branche saute la seconde."""
    teaser = Segment(21.337, 22.891)
    edl = Edl(teaser, [Segment(0.0, 4.12)])
    args = teaser_input_args(Path("s.mp4"), start_s=5.0, teaser=teaser)
    duree = float(args[args.index("-t") + 1])
    video = video_cut(edl, clip_dur_s=30.0, fps=FPS, teaser_index=2)
    m = re.match(r"\[2:v\]trim=end=([\d.]+),", video)
    assert m, video
    assert float(m.group(1)) == duree == 2.554
    assert audio_cut(edl, clip_dur_s=30.0, teaser_index=2).startswith(
        "[2:a]atrim=start=1.000,asetpts=PTS-STARTPTS[ac0];")


def test_audio_cut_without_teaser_is_byte_identical_to_the_body_only_graph():
    assert audio_cut(BODY_ONLY_EDL, clip_dur_s=11.0) == (
        "[0:a]atrim=start=0.000:end=4.120,asetpts=PTS-STARTPTS[ac0];"
        "[0:a]atrim=start=6.000:end=9.400,asetpts=PTS-STARTPTS[ac1];"
        "[ac0][ac1]concat=n=2:v=0:a=1[acut]")


def test_teaser_input_args_seek_the_source_on_the_teaser_alone():
    """-ss = debut du clip + debut du teaser - 1 s de recul, -t = duree du teaser
    + recul, arrondis a la milliseconde (31.12 - 29.5 vaut 1.620000000000001 en
    flottant)."""
    args = teaser_input_args(Path("C:/v/src.mp4"), start_s=12.3457,
                             teaser=Segment(29.5, 31.12))
    assert args == ["-ss", "40.846", "-t", "2.62", "-i", str(Path("C:/v/src.mp4"))]


def test_video_cut_empty_for_single_full_segment_without_teaser():
    assert video_cut(Edl(None, [Segment(0.0, 20.0)]), clip_dur_s=20.0, fps=FPS) == ""


def test_audio_cut_empty_for_single_full_segment_without_teaser():
    assert audio_cut(Edl(None, [Segment(0.0, 20.0)]), clip_dur_s=20.0) == ""


def test_whole_clip_under_zoompan_still_goes_through_the_grid():
    """Revue A2, fix round 1 : sans coupe, la branche jeu gardait les instants
    de la source apres -ss et zoompan re-datait la branche cam sur sa grille ;
    cam et jeu divergeaient (420 images sur 422 a 29,97 ips). Sous zoompan, la
    video passe par la grille en un seul morceau et l'audio par [acut] tel
    quel : build_render_cmd recoit les deux graphes ensemble. Une EDL coupee
    ne change pas."""
    edl = Edl(None, [Segment(0.0, 20.0)])
    assert video_cut(edl, clip_dur_s=20.0, fps=FPS, zoompan=True) == (
        "[0:v]trim=start=0.000:end=20.000,"
        "setpts=PTS-STARTPTS+round((STARTPTS*TB-0.000000)*30/1)*1/30/TB,"
        "tpad=stop_mode=clone:stop=-1,fps=30/1:start_time=0,trim=end_frame=600[vc0];"
        "[vc0]concat=n=1:v=1:a=0[vcut];[vcut]fps=30/1,split=2[vcuta][vcutb]")
    assert audio_cut(edl, clip_dur_s=20.0, zoompan=True) == "[0:a]anull[acut]"
    assert video_cut(TEMPLATE_EDL, clip_dur_s=11.0, fps=FPS, teaser_index=TEASER_INDEX,
                     zoompan=True) == VIDEO_GOLDEN
    assert audio_cut(TEMPLATE_EDL, clip_dur_s=11.0, teaser_index=TEASER_INDEX,
                     zoompan=True) == AUDIO_GOLDEN


def test_video_cut_still_cuts_when_multiple_body_segments_and_no_teaser():
    edl = Edl(None, [Segment(0.0, 4.0), Segment(5.0, 9.0)])
    fc = video_cut(edl, clip_dur_s=9.0, fps=FPS)
    assert fc.startswith("[0:v]trim=start=0.000:end=4.000,"
                         "setpts=PTS-STARTPTS+round((STARTPTS*TB-0.000000)*30/1)*1/30/TB,tpad=")
    assert "concat=n=2:v=1:a=0[vcut];[vcut]fps=30/1,split=2[vcuta][vcutb]" in fc


def test_video_cut_still_cuts_when_only_a_teaser_and_one_body_segment():
    edl = Edl(Segment(1.0, 2.0), [Segment(3.0, 10.0)])
    fc = video_cut(edl, clip_dur_s=10.0, fps=FPS, teaser_index=1)
    assert fc.count("[vc0]") >= 1 and fc.count("[vc1]") >= 1
    assert "concat=n=2:v=1:a=0[vcut]" in fc


def test_a_teaser_before_a_body_spanning_the_whole_clip_still_cuts():
    """Revue finale V6 : le test ci-dessus garde un corps qui commence a 3.0,
    deja refuse par la clause de bord ; seule l'integration voyait disparaitre
    le teaser quand la clause `edl.teaser is not None` de _covers_whole_clip
    sautait. Ici le corps couvre tout le clip : seul le teaser justifie la
    coupe."""
    edl = Edl(Segment(4.0, 5.5), [Segment(0.0, 10.0)])
    video = video_cut(edl, clip_dur_s=10.0, fps=FPS, teaser_index=2)
    audio = audio_cut(edl, clip_dur_s=10.0, teaser_index=2)
    assert video.startswith("[2:v]trim=end=2.500,"), video
    assert "[0:v]trim=start=0.000:end=10.000," in video, video
    assert audio == ("[2:a]atrim=start=1.000,asetpts=PTS-STARTPTS[ac0];"
                     "[0:a]atrim=start=0.000:end=10.000,asetpts=PTS-STARTPTS[ac1];"
                     "[ac0][ac1]concat=n=2:v=0:a=1[acut]")


def test_video_cut_still_cuts_when_single_segment_does_not_span_the_full_clip():
    """Repro controleur (finding 1) : mots [3.0..18.0] sur un clip de 20 s ->
    build_cuts produit un seul segment [2.88, 18.12], PAS [0, 20] -- la tete et
    la queue restent bel et bien coupees dans l'EDL. -ss/-t bornent l'entree
    ffmpeg au clip, pas au trim interne de l'EDL : sans ce garde, le raccourci
    "un seul segment = rien a couper" fait fuir 2.88 s de tete + 1.88 s de queue
    dans le rendu. Le raccourci ne doit se declencher que si le segment couvre
    reellement tout le clip (src_start<=eps et src_end proche de clip_dur_s)."""
    edl = Edl(None, [Segment(2.88, 18.12)])
    fc = video_cut(edl, clip_dur_s=20.0, fps=FPS)
    assert fc != ""
    assert fc.startswith("[0:v]trim=start=1.880:end=18.120,"
                         "setpts=PTS-STARTPTS+round((STARTPTS*TB-2.880000)*30/1)*1/30/TB,")
    assert "concat=n=1:v=1:a=0[vcut];[vcut]fps=30/1,split=2[vcuta][vcutb]" in fc


@pytest.mark.parametrize("seg", [Segment(2.0, 20.0), Segment(0.0, 18.0), Segment(0.0, 19.99)])
def test_a_single_segment_short_of_one_edge_still_cuts(seg):
    """Revue du lot B : le segment [2.88, 18.12] ci-dessus manque les deux bords
    a la fois, et retirer une seule des deux clauses de bord de
    _covers_whole_clip, la rendre unilaterale ou elargir _EPS a 0,5 s passait
    partout. Ici un seul bord manque, de 2 s ou de 10 ms."""
    edl = Edl(None, [seg])
    assert video_cut(edl, clip_dur_s=20.0, fps=FPS) != ""
    assert audio_cut(edl, clip_dur_s=20.0) != ""


def test_audio_cut_still_cuts_when_single_segment_does_not_span_the_full_clip():
    edl = Edl(None, [Segment(2.88, 18.12)])
    fc = audio_cut(edl, clip_dur_s=20.0)
    assert fc != ""
    assert fc.startswith("[0:a]atrim=start=2.880:end=18.120,asetpts=PTS-STARTPTS[ac0];")
    assert "concat=n=1:v=0:a=1[acut]" in fc


def test_video_cut_empty_tolerates_floating_point_noise_at_the_boundaries():
    """Meme contrat que edl._EPS/timemap._EPS : une marge de 1e-9 absorbe le bruit
    de representation flottante sans rouvrir la faille (un vrai trim de 1 ms
    resterait largement au-dela de cette marge)."""
    edl = Edl(None, [Segment(0.0000000001, 19.9999999999)])
    assert video_cut(edl, clip_dur_s=20.0, fps=FPS) == ""
    assert audio_cut(edl, clip_dur_s=20.0) == ""
