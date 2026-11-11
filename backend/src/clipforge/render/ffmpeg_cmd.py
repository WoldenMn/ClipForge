from pathlib import Path

from clipforge.render.montage.motion import X_EXPR_DEFAULT, Y_EXPR_DEFAULT

TOP_H = 576      # facecam 1080x576 (30 %)
BOT_H = 1344     # gameplay 1080x1344 (70 %)

EMOJI_W = 160
EMOJI_X = "main_w-200"
EMOJI_Y = "180"

NVENC = ["-c:v", "h264_nvenc", "-preset", "p6", "-tune", "hq", "-rc", "vbr",
         "-b:v", "10M", "-maxrate", "12M", "-bufsize", "20M",
         "-multipass", "qres", "-spatial-aq", "1", "-rc-lookahead", "20",
         "-bf", "3", "-b_ref_mode", "middle", "-profile:v", "high"]
X264 = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20"]


def even(n: int) -> int:
    return n - (n % 2)


def _cam_branch(profile: dict, zoom_expr: str, fps: float | str, *, src: str = "0:v",
                x_expr: str | None = None, y_expr: str | None = None) -> str:
    """Facecam recadree, mise a l'echelle, puis zoomee sur les pics audio.

    `x_expr`/`y_expr` par defaut a None -> expressions statiques de motion.py
    (pas de secousse). render/montage_plan.py y passe les expressions
    organiques compilees par render.montage.motion.build_motion.
    """
    x_expr = X_EXPR_DEFAULT if x_expr is None else x_expr
    y_expr = Y_EXPR_DEFAULT if y_expr is None else y_expr
    cw, ch = even(profile["cam_w"]), even(profile["cam_h"])
    chain = (f"[{src}]crop={cw}:{ch}:{profile['cam_x']}:{profile['cam_y']},"
             f"scale=1080:{TOP_H}:flags=lanczos")
    if zoom_expr != "1":
        chain += (f",zoompan=z='{zoom_expr}':d=1:x='{x_expr}':"
                  f"y='{y_expr}':"
                  f"s=1080x{TOP_H}:fps={fps}")
    return chain + "[top]"


def _video_graph(ass_name: str, profile: dict, zoom_expr: str, fps: float | str,
                 emoji: tuple[Path, float, float] | None, emoji_index: int, *,
                 src_cam: str = "0:v", src_game: str = "0:v",
                 x_expr: str | None = None, y_expr: str | None = None) -> str:
    gw, gh = even(profile["game_w"]), even(profile["game_h"])
    parts = [
        _cam_branch(profile, zoom_expr, fps, src=src_cam, x_expr=x_expr, y_expr=y_expr),
        (f"[{src_game}]crop={gw}:{gh}:{profile['game_x']}:{profile['game_y']},"
         f"scale=1080:{BOT_H}:flags=lanczos[bot]"),
        "[top][bot]vstack=inputs=2[stacked]",
    ]
    if emoji is None:
        return ";".join([*parts, f"[stacked]subtitles={ass_name}[v]"])
    _, t0, t1 = emoji
    return ";".join([
        *parts,
        f"[stacked]subtitles={ass_name}[v0]",
        f"[{emoji_index}:v]scale={EMOJI_W}:-1[emo]",
        (f"[v0][emo]overlay=x={EMOJI_X}:y={EMOJI_Y}:shortest=1:"
         f"enable='between(t,{t0:.3f},{t1:.3f})'[v]"),
    ])


def build_render_cmd(source: Path, out: Path, ass_name: str, *, start_s: float,
                     end_s: float, profile: dict, fps: float | str, zoom_expr: str = "1",
                     emoji: tuple[Path, float, float] | None = None,
                     sfx_inputs: list[str] | None = None, audio_graph: str = "",
                     use_nvenc: bool = True, video_cut: str = "", audio_cut: str = "",
                     x_expr: str | None = None, y_expr: str | None = None,
                     teaser_input: list[str] | None = None,
                     graph_file: Path | None = None) -> list[str]:
    """Commande ffmpeg complete. A executer avec cwd = dossier du job (ass_name relatif).

    Une seule passe : recadrage, punch-in (`zoom_expr`), empilement, sous-titres,
    emoji d'accroche et chaine audio (`sfx_inputs` + `audio_graph`, produits par
    render.sfx.audio_filter). Les entrees d'effets viennent JUSTE apres la source :
    `audio_graph` les numerote `[1:a]`, `[2:a]`… et le PNG de l'emoji passe apres
    elles, sans quoi il les decalerait toutes.

    `video_cut`/`audio_cut` (produits par render.filtergraph) : `-ss start_s -t
    (end_s-start_s)` bornent deja l'entree sur le clip source, donc les temps de
    l'EDL qu'ils encodent tombent directement sur cette entree, sans decalage.
    A vide (defaut) : comportement V5 inchange, branches cam/jeu lues sur `0:v`.
    Non vides : prefixes au filter_complex, branches cam/jeu lues sur `vcuta`/
    `vcutb` (produits par video_cut) et `audio_graph` peut lire `[acut]`.

    Les deux doivent etre coupes ENSEMBLE ou pas du tout : l'un non vide et
    l'autre vide produirait une piste video coupee sur une piste audio pleine
    longueur (ou l'inverse), une desync audio/video silencieuse (rc=0, mesure
    finding 3 review Task 7 : video 5 s, audio 16 s) -- ValueError plutot que
    ce piege muet.

    `fps` : cadence de zoompan. Le repli V5 passe le flottant de la VOD, le
    montage V6 le texte num/den de render.frame_grid.frame_grid, celui de chaque
    fps= de `video_cut`.

    `teaser_input` (render.filtergraph.teaser_input_args, vide par defaut) :
    entree dediee au teaser, placee juste apres les effets et avant l'emoji,
    dont l'index doit donc la compter.

    `graph_file` : fichier ou le filter_complex est ecrit, lu par ffmpeg via
    -/filter_complex. Chaque segment coupe allonge le graphe d'environ 280
    caracteres (video, audio, zoom) ; en ligne, la commande depassait la limite
    de CreateProcess (32 767 caracteres) vers 105 segments (revue A2, WinError
    206). None (repli V5) : graphe en ligne, commande inchangee.
    """
    if bool(video_cut) != bool(audio_cut):
        raise ValueError(
            "video_cut et audio_cut incoherents : l'un est vide, l'autre non "
            "(ils doivent etre coupes ensemble ou pas du tout, sinon video et "
            "audio se desynchronisent silencieusement au rendu)"
        )
    sfx_inputs = sfx_inputs or []
    teaser_input = teaser_input or []
    src_cam = "vcuta" if video_cut else "0:v"
    src_game = "vcutb" if video_cut else "0:v"
    fc = _video_graph(ass_name, profile, zoom_expr, fps, emoji,
                      1 + sfx_inputs.count("-i") + teaser_input.count("-i"),
                      src_cam=src_cam, src_game=src_game, x_expr=x_expr, y_expr=y_expr)
    if video_cut:
        fc = video_cut + ";" + fc
    if audio_cut:
        fc += ";" + audio_cut
    if audio_graph:
        fc += ";" + audio_graph
    emoji_input = ["-loop", "1", "-i", str(emoji[0])] if emoji else []
    audio_map = ["-map", "[aout]" if audio_graph else "0:a?"]
    if graph_file is None:
        graphe = ["-filter_complex", fc]
    else:
        graph_file.write_text(fc, encoding="utf-8")
        graphe = ["-/filter_complex", str(graph_file)]
    return ["ffmpeg", "-y", "-ss", str(start_s), "-t", str(round(end_s - start_s, 3)),
            "-i", str(source), *sfx_inputs, *teaser_input, *emoji_input,
            *graphe, "-map", "[v]", *audio_map,
            *(NVENC if use_nvenc else X264), "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(out)]
