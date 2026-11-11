from dataclasses import dataclass, field

from clipforge.models import SubtitleStyle
from clipforge.pipeline.segmenter import Word
from clipforge.render.ass import _sanitize, hex_to_ass, sec_to_ass
from clipforge.text import is_continuation, join_words

HOOK_POS, CAP_POS, SIGN_POS, HOOK_MAX_S = (540, 250), (540, 1180), (540, 606), 2.8

# Un enregistrement ASS (Format:, Style:) tient sur UNE ligne : le couper casserait
# le fichier que libass lit. D'ou le noqa qui ferme ce gabarit.
HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Pop,{font},{pop_size},{primary},{secondary},{outline},&H80000000,-1,0,0,0,100,100,0,0,1,{outline_px},3,5,90,90,0,1
Style: HookRim,{font},{hook_size},&HFF000000,&HFF000000,{hook_outline},&HB0080808,-1,0,0,0,100,100,0,0,3,5,0,8,220,220,0,1
Style: Hook,{font},{hook_size},{primary},{secondary},{outline},&HB0080808,-1,0,0,0,100,100,0,0,3,3,0,8,220,220,0,1
Style: Sign,{font},34,&H60FFFFFF,{secondary},&H60000000,&H00000000,-1,0,0,0,100,100,3,0,1,1,0,5,90,90,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""  # noqa: E501

POP_IN = "\\fscx30\\fscy30\\t(0,70,\\fscx112\\fscy112)\\t(70,130,\\fscx100\\fscy100)"

# Tags par tier d'emphase (Task 6), restaures apres coup ; "normal" n'a aucun tag
# (sortie V5 intacte)
_TIER_SCALE = {"bas": 0.85, "haut": 1.35}
_TIER_COLOR = {"bas": "&HD8D8D8&", "haut": "&HFFFFFF&"}
# Un pas de TimeMap : mots et coupes y sont arrondis a la ms chacun de son cote
_CUT_TOL_S = 0.001
# Force d'un tier, pour agreger une unite d'affichage (cf. _units). Un tier
# inconnu vaut "normal", comme _tier_tags le rend deja sans tag.
_TIER_RANK = {"bas": 0, "normal": 1, "haut": 2}


@dataclass(frozen=True)
class PopGroup:
    start: float
    end: float
    words: list[str]
    key_index: int
    tiers: list[str] = field(default_factory=list)


def _units(pairs: list[tuple[Word, str]]) -> list[tuple[Word, str]]:
    """Fond chaque mot de continuation dans celui qui le precede.

    L'unite d'AFFICHAGE n'est pas le mot de la base : faster-whisper rend
    « c » + « 'est » en deux mots, et les compter comme deux laissait une
    coupure de groupe passer au milieu d'une elision (« ! NON, C » puis
    « 'EST PAS MOI », rendu reel du 2026-09-18). Fusionner AVANT les regles de
    coupe est ce qui rend cette separation impossible, quelle que soit la
    regle — plafond de mots, plafond de signes ou pause.

    Le tier d'emphase de l'unite est le PLUS FORT de ses moities : un seul jeu
    de tags ASS s'applique a l'unite entiere, et rapetisser « c'est » parce que
    le « c » atone tombe sous p25 effacerait l'emphase portee par la syllabe qui
    tient le sens. L'emphase mesure une saillance : son agregat est le maximum.
    """
    unites: list[list[tuple[Word, str]]] = []
    for pair in pairs:
        if unites and is_continuation(pair[0].text):
            unites[-1].append(pair)
        else:
            unites.append([pair])
    return [(Word(start=u[0][0].start, end=u[-1][0].end,
                  text=join_words([w.text for w, _ in u])),
             max((t for _, t in u), key=lambda t: _TIER_RANK.get(t, 1)))
            for u in unites]


def group_words(words: list[Word], *, tiers: list[str] | None = None,
                cut_times: list[float] | None = None, max_words: int = 3,
                max_gap_s: float = 0.35, max_chars: int = 16) -> list[PopGroup]:
    """Regroupe les mots en paquets de 1 a max_words, coupes sur pause/longueur.
    tiers (Task 6) doit etre aligne sur words -- ecart : ValueError (pas assert,
    strippe par python -O, et str(AssertionError()) vide masquerait l'erreur en
    base, cf. queue.py). Seul site de validation et de filtre zip du module ;
    build_pop_ass delegue ici sans filtrer ni valider de son cote.

    Les mots sont d'abord fondus en unites d'affichage (_units) : une elision ou
    un trait d'union ne peut donc etre separe par aucune des coupes ci-dessous,
    et max_words comme max_chars comptent ce qui s'ecrit, pas ce que la
    transcription a decoupe.

    cut_times : instants de coupe de la timeline de sortie (TimeMap.cut_times).
    Une coupe retire la pause qui separait deux phrases et l'ecart de sortie
    tombe sous max_gap_s (<= 2 x PAD_S) : tout instant de coupe pose entre deux
    unites, a _CUT_TOL_S pres, ouvre donc un groupe. Une coupe posee entre les
    deux moities d'une elision n'en separe pas l'unite : le mot reste entier.
    None (V5) : rien ne change."""
    if tiers is not None:
        if len(tiers) != len(words):
            raise ValueError(f"tiers ({len(tiers)}) et words ({len(words)}) desalignes")
    else:
        tiers = ["normal"] * len(words)
    pairs = _units([(w, t) for w, t in zip(words, tiers) if w.text.strip()])

    groups: list[list[tuple[Word, str]]] = []
    cur: list[tuple[Word, str]] = []
    coupes = cut_times or []
    for w, t in pairs:
        chars = sum(len(x.text) for x, _ in cur) + len(w.text)
        if cur and (len(cur) >= max_words or w.start - cur[-1][0].end > max_gap_s
                    or chars > max_chars
                    or any(cur[-1][0].end - _CUT_TOL_S <= c <= w.start + _CUT_TOL_S
                           for c in coupes)):
            groups.append(cur)
            cur = []
        cur.append((w, t))
    if cur:
        groups.append(cur)
    out = []
    for g in groups:
        lengths = [len(w.text) for w, _ in g]
        key = max(range(len(g)), key=lambda i: (lengths[i], i))
        out.append(PopGroup(start=g[0][0].start, end=g[-1][0].end,
                             words=[w.text for w, _ in g], key_index=key,
                             tiers=[t for _, t in g]))
    return out


def _tier_tags(tier: str, style: SubtitleStyle) -> tuple[str, str]:
    """Prefixe/suffixe ASS pour un tier : suffixe restaure toujours taille/bord/couleur V5.
    Un tier inconnu leve ValueError : avale, un renommage d'un seul cote
    (emphasis.py) effacait l'emphase de tous les clips sans un mot."""
    if tier == "normal":
        return "", ""
    if tier not in _TIER_SCALE:
        raise ValueError(f"tier d'emphase inconnu : {tier!r} "
                         f"(connus : normal, {', '.join(_TIER_SCALE)})")
    size = round(_TIER_SCALE[tier] * style.font_size)
    color = _TIER_COLOR[tier]
    if tier == "haut":
        prefix = f"{{\\fs{size}\\bord{style.outline_px + 1}\\1c{color}}}"
    else:
        prefix = f"{{\\fs{size}\\1c{color}}}"
    suffix = (f"{{\\fs{style.font_size}\\bord{style.outline_px}"
              f"\\1c{hex_to_ass(style.primary_hex)}}}")
    return prefix, suffix


def _key_wrapped(text: str, style: SubtitleStyle) -> str:
    return (f"{{\\1c{hex_to_ass(style.highlight_hex)}}}{text}"
            f"{{\\1c{hex_to_ass(style.primary_hex)}}}")


def build_pop_ass(words: list[Word], style: SubtitleStyle, *, clip_start_s: float,
                   clip_dur_s: float, hook: str | None, handle: str | None,
                   tiers: list[str] | None = None,
                   cut_times: list[float] | None = None) -> str:
    """Sous-titres pop (paquets de 1-3 mots), accroche et signature, temps RELATIFS au clip.
    tiers (Task 6) : validation d'alignement et filtre des mots vides delegues
    a group_words (site unique, cf. sa docstring) -- words/tiers passent tels quels,
    comme cut_times (coupes de montage, cf. group_words)."""
    # BorderStyle=3 (boite opaque) peint la boite ENTIERE dans OutlineColour, pas
    # un simple liseret -- verifie sur un rendu reel (Task 10, D4 round 1) : y
    # mettre highlight_hex directement donne une boite ambre PLEINE, illisible en
    # blanc-sur-ambre et infidele a la maquette (boite sombre a liseret ambre).
    # D'ou la technique a deux calques, superposition d'egale position/anim :
    # HookRim (texte invisible, boite plus GRANDE de +2px, peinte en ambre) sous
    # Hook (boite normale, sombre) -- seul le debord de 2px de Rim reste visible,
    # comme un liseret. Ordre garanti par le champ Layer des Dialogue (round 2) :
    # un Layer plus eleve se dessine PAR-DESSUS un Layer plus bas (verifie par
    # rendu isole avant d'ecrire ce code), donc Rim=1 sous Hook=2.
    out = HEADER.format(
        font=style.font_name, pop_size=style.font_size, hook_size=64,
        primary=hex_to_ass(style.primary_hex), secondary=hex_to_ass(style.highlight_hex),
        outline=hex_to_ass(style.outline_hex),
        hook_outline=hex_to_ass(style.highlight_hex), outline_px=style.outline_px)

    for group in group_words(words, tiers=tiers, cut_times=cut_times):
        start = max(0.0, group.start - clip_start_s)
        end = min(clip_dur_s, max(0.0, group.end - clip_start_s))
        parts = []
        for i, word in enumerate(group.words):
            text = _sanitize(word)
            text = text.upper() if style.uppercase else text
            if i == group.key_index:
                text = _key_wrapped(text, style)
            # prefixe de tier, mot (\1c highlight ecrase celui du tier sur le mot-cle), suffixe V5
            prefix, suffix = _tier_tags(group.tiers[i], style)
            parts.append(f"{prefix}{text}{suffix}")
        out += (f"Dialogue: 0,{sec_to_ass(start)},{sec_to_ass(end)},Pop,,0,0,0,,"
                f"{{\\pos({CAP_POS[0]},{CAP_POS[1]}){POP_IN}}}{' '.join(parts)}\n")

    if hook:
        hook_end = min(HOOK_MAX_S, clip_dur_s)
        hook_text = _sanitize(hook)
        hook_text = hook_text.upper() if style.uppercase else hook_text
        hook_tags = f"\\pos({HOOK_POS[0]},{HOOK_POS[1]})\\fad(120,180){POP_IN}"
        # Meme position/anim/texte sur les deux calques : sinon le retour a la
        # ligne pourrait diverger entre Rim et Hook et le liseret se desalignerait.
        out += (f"Dialogue: 1,{sec_to_ass(0.0)},{sec_to_ass(hook_end)},HookRim,,0,0,0,,"
                f"{{{hook_tags}}}{hook_text}\n")
        out += (f"Dialogue: 2,{sec_to_ass(0.0)},{sec_to_ass(hook_end)},Hook,,0,0,0,,"
                f"{{{hook_tags}}}{hook_text}\n")

    if handle:
        sign_text = _sanitize(handle).upper()
        out += (f"Dialogue: 0,{sec_to_ass(0.0)},{sec_to_ass(clip_dur_s)},Sign,,0,0,0,,"
                f"{{\\pos({SIGN_POS[0]},{SIGN_POS[1]})}}{sign_text}\n")

    return out
