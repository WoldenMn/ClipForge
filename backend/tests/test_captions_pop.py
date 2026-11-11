import pytest

from clipforge.models import SubtitleStyle
from clipforge.pipeline.segmenter import Word
from clipforge.render.ass import hex_to_ass, sec_to_ass
from clipforge.render.captions_pop import CAP_POS, HOOK_MAX_S, PopGroup, build_pop_ass, group_words
from clipforge.render.montage.emphasis import word_tiers


def _w(t, txt, dur=0.3):
    return Word(start=t, end=t + dur, text=txt)


STYLE = SubtitleStyle()  # defauts


def test_groups_break_on_pause_count_and_length():
    words = [_w(0.0, "il"), _w(0.35, "manque"), _w(0.72, "de"),      # 3 -> coupe
             _w(1.1, "mourir"),                                       # gap 0.38 -> coupe avant
             _w(2.0, "incroyablement")]                               # 14 chars, seul
    gs = group_words(words)
    assert [g.words for g in gs] == [["il", "manque", "de"], ["mourir"], ["incroyablement"]]
    assert all(isinstance(g, PopGroup) for g in gs)


def test_key_word_is_the_longest_of_the_group():
    gs = group_words([_w(0.0, "une"), _w(0.4, "attaque"), _w(0.8, "la")])
    assert gs[0].key_index == 1


def test_pop_ass_animates_scale_and_colors_the_key_word():
    ass = build_pop_ass([_w(10.0, "attaque"), _w(10.4, "cardiaque")], STYLE,
                         clip_start_s=10.0, clip_dur_s=20.0, hook=None, handle=None)
    assert "\\t(0,70,\\fscx112\\fscy112)" in ass          # pop d'entree
    assert "\\t(70,130,\\fscx100\\fscy100)" in ass        # retombee
    assert ass.count("\\1c&H") >= 1                        # mot-cle teinte
    assert f"\\pos({CAP_POS[0]},{CAP_POS[1]})" in ass
    assert "ATTAQUE" in ass                                # uppercase par defaut


def test_hook_lives_only_on_the_first_seconds():
    ass = build_pop_ass([_w(5.0, "go")], STYLE, clip_start_s=5.0, clip_dur_s=30.0,
                         hook="IL MANQUE DE MOURIR", handle=None)
    ligne = next(l for l in ass.splitlines() if ",Hook," in l)
    # Layer 2 (round 2, D4) : Hook doit se dessiner PAR-DESSUS HookRim (layer 1).
    assert ligne.startswith(f"Dialogue: 2,{sec_to_ass(0.0)},{sec_to_ass(HOOK_MAX_S)},")
    assert "MOURIR" in ligne


def test_signature_spans_the_whole_clip_and_empty_handle_means_none():
    avec = build_pop_ass([_w(0.0, "go")], STYLE, clip_start_s=0.0, clip_dur_s=21.5,
                          hook=None, handle="twitch.tv/pseudo")
    sans = build_pop_ass([_w(0.0, "go")], STYLE, clip_start_s=0.0, clip_dur_s=21.5,
                          hook=None, handle=None)
    vide = build_pop_ass([_w(0.0, "go")], STYLE, clip_start_s=0.0, clip_dur_s=21.5,
                          hook=None, handle="")
    ligne = next(l for l in avec.splitlines() if ",Sign," in l)
    assert "0:00:21.50" in ligne and "TWITCH.TV/PSEUDO" in ligne
    assert ",Sign," not in sans
    assert ",Sign," not in vide


def test_hook_text_is_sanitized_and_short_clip_shortens_hook():
    ass = build_pop_ass([_w(0.0, "go")], STYLE, clip_start_s=0.0, clip_dur_s=1.5,
                         hook="a{b}c\\d", handle=None)
    ligne = next(l for l in ass.splitlines() if ",Hook," in l)
    assert "{b}" not in ligne and "\\d" not in ligne.split(",,")[-1]
    assert "0:00:01.50" in ligne                           # min(HOOK_MAX_S, dur)


def test_pop_caption_end_is_clamped_to_clip_duration():
    ass = build_pop_ass([_w(19.5, "fin", dur=1.0)], STYLE, clip_start_s=0.0,
                         clip_dur_s=20.0, hook=None, handle=None)
    ligne = next(l for l in ass.splitlines() if ",Pop," in l)
    assert ligne.startswith(f"Dialogue: 0,{sec_to_ass(19.5)},{sec_to_ass(20.0)},Pop,")


def test_hook_margins_are_pinned_wide_enough_to_clear_the_emoji():
    """La marge EST la garantie anti-collision : d'ou une valeur absolue epinglee.

    L'emoji d'accroche est overlaye APRES les sous-titres (ffmpeg_cmd.py), dans
    une boite qui commence a x=880 (EMOJI_X = main_w-200) : ce qui la touche
    n'est pas melange, il est RECOUVERT — l'accroche perd ses dernieres lettres.

    La marge borne la largeur de retour a la ligne (1080-2M), et l'accroche est
    centree sur x=540, donc le x maximal peint vaut 540 + (1080-2M)/2 = 1080-M.
    Il faut 1080-M <= 880, soit M >= 200 ; 220 laisse 20 px de garde. Mesure
    libass a t=1 s sur des accroches de 18 a 39 caracteres : a M=120 le x
    maximal monte a 951 et une bande d'accroches d'une seule ligne recouvre la
    boite ; a M=220 il plafonne a 858, aucune longueur ne l'atteint. Baisser
    cette valeur rouvre la collision, d'ou ce test qui la fige.
    """
    ass = build_pop_ass([_w(0.0, "go")], STYLE, clip_start_s=0.0, clip_dur_s=10.0,
                         hook="ELLE SORT LE COUTEAU LA", handle=None)
    style_hook = next(l for l in ass.splitlines() if l.startswith("Style: Hook,"))
    assert style_hook.endswith(",8,220,220,0,1")   # Alignment, MarginL, MarginR, MarginV


def test_hook_style_stays_dark_after_the_round_2_revert():
    """D4 round 2 : peindre OutlineColour de Hook en ambre (round 1) donnait une
    boite PLEINE ambre (BorderStyle=3 peint toute la boite dans ce champ, pas un
    liseret -- confirme sur un rendu reel), illisible en blanc-sur-ambre et
    infidele a la maquette. Hook revient donc a outline_hex (sombre) ; l'ambre se
    fait desormais sur un calque separe (HookRim, cf. tests plus bas)."""
    ass = build_pop_ass([_w(0.0, "go")], STYLE, clip_start_s=0.0, clip_dur_s=10.0,
                         hook="GO", handle=None)
    style_hook = next(l for l in ass.splitlines() if l.startswith("Style: Hook,"))
    outline_colour = style_hook.split(",")[5]
    assert outline_colour == hex_to_ass(STYLE.outline_hex)
    assert outline_colour != hex_to_ass(STYLE.highlight_hex)


def test_pop_style_outline_colour_is_unaffected_by_the_hook_fix():
    """Non-regression : D4 cible uniquement les styles Hook/HookRim, pas Pop (le
    cerne des mots-cle pop reste noir, comme sur les images de validation)."""
    ass = build_pop_ass([_w(0.0, "go")], STYLE, clip_start_s=0.0, clip_dur_s=10.0,
                         hook="GO", handle=None)
    style_pop = next(l for l in ass.splitlines() if l.startswith("Style: Pop,"))
    assert style_pop.split(",")[5] == hex_to_ass(STYLE.outline_hex)


def test_hook_rim_style_has_invisible_text_and_amber_outline():
    """D4 round 2 : HookRim ne doit jamais montrer de texte (PrimaryColour alpha
    FF = transparent) -- seule sa boite (OutlineColour = highlight_hex, ambre)
    doit se voir, et seulement la ou elle DEBORDE de la boite Hook par-dessus."""
    ass = build_pop_ass([_w(0.0, "go")], STYLE, clip_start_s=0.0, clip_dur_s=10.0,
                         hook="GO", handle=None)
    style_rim = next(l for l in ass.splitlines() if l.startswith("Style: HookRim,"))
    fields = style_rim.split(",")
    assert fields[3] == "&HFF000000"                       # PrimaryColour transparente
    assert fields[5] == hex_to_ass(STYLE.highlight_hex)     # OutlineColour ambre
    assert fields[15] == "3"                                # BorderStyle : boite opaque


def test_hook_rim_outline_width_is_wider_than_hooks_own():
    """La largeur d'Outline EST le rim : HookRim doit deborder de quelques px de
    part et d'autre de la boite Hook, sinon rien ne depasse et rien ne se voit."""
    ass = build_pop_ass([_w(0.0, "go")], STYLE, clip_start_s=0.0, clip_dur_s=10.0,
                         hook="GO", handle=None)
    style_hook = next(l for l in ass.splitlines() if l.startswith("Style: Hook,"))
    style_rim = next(l for l in ass.splitlines() if l.startswith("Style: HookRim,"))
    outline_hook = int(style_hook.split(",")[16])
    outline_rim = int(style_rim.split(",")[16])
    assert outline_rim > outline_hook


def test_hook_emits_rim_then_hook_with_rim_on_the_lower_layer():
    """Ordre de calque verifie par rendu isole avant d'ecrire ce test : un Layer
    plus eleve se dessine par-dessus un Layer plus bas dans libass. HookRim doit
    donc porter un Layer strictement inferieur a celui de Hook, et apparaitre
    avant lui dans le flux d'evenements (l'ordre du fichier n'est pas la garantie
    -- le Layer l'est -- mais les deux doivent concorder pour rester lisible)."""
    ass = build_pop_ass([_w(0.0, "go")], STYLE, clip_start_s=0.0, clip_dur_s=10.0,
                         hook="GO", handle=None)
    lignes = [l for l in ass.splitlines() if l.startswith("Dialogue:") and
             (",HookRim," in l or ",Hook," in l)]
    assert len(lignes) == 2
    rim, hook = lignes
    assert ",HookRim," in rim and ",Hook," in hook
    assert int(rim.split(",")[0].split(": ")[1]) < int(hook.split(",")[0].split(": ")[1])


def test_hook_rim_shares_position_fade_and_text_with_hook():
    """Meme boite visuelle : si Rim et Hook divergeaient de position/anim/texte,
    le retour a la ligne pourrait differer entre les deux et le liseret se
    desaligner de la boite qu'il est cense entourer."""
    ass = build_pop_ass([_w(0.0, "go")], STYLE, clip_start_s=0.0, clip_dur_s=10.0,
                         hook="ELLE SORT LE COUTEAU LA", handle=None)
    rim = next(l for l in ass.splitlines() if ",HookRim," in l)
    hook = next(l for l in ass.splitlines() if l.startswith("Dialogue:") and ",Hook," in l)
    # "0,0,0,," = MarginL,MarginR,MarginV,Effect (toujours ces valeurs fixes ici) :
    # tout ce qui suit est le bloc de tags + texte, identique sur les deux calques.
    tags_and_text = lambda l: l.split("0,0,0,,", 1)[1]
    assert tags_and_text(rim) == tags_and_text(hook)


def test_handle_text_is_sanitized():
    ass = build_pop_ass([_w(0.0, "go")], STYLE, clip_start_s=0.0, clip_dur_s=10.0,
                         hook=None, handle="a{b}\\c")
    ligne = next(l for l in ass.splitlines() if ",Sign," in l)
    assert "{b}" not in ligne and "\\c" not in ligne.split(",,")[-1]


def test_ass_sans_tiers_est_identique_a_v5():
    """Golden fige AVANT Task 6 (git show 19a5ad0:.../captions_pop.py, module V5
    execute isolement sur cette meme fixture) : compare le rendu d'aujourd'hui
    (tiers=None) a une reference figee, pas a lui-meme -- une regression du
    chemin sans tiers ferait echouer ce test, ce qu'une comparaison de
    build_pop_ass avec build_pop_ass ne peut jamais faire."""
    mots = [_w(10.0, "attaque"), _w(10.4, "cardiaque")]
    apres = build_pop_ass(mots, STYLE, clip_start_s=10.0, clip_dur_s=20.0,
                           hook="ELLE SORT LE COUTEAU LA", handle="twitch.tv/pseudo",
                           tiers=None)
    golden = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        "PlayResX: 1080\n"
        "PlayResY: 1920\n"
        "WrapStyle: 0\n"
        "ScaledBorderAndShadow: yes\n"
        "\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        "Style: Pop,Segoe UI,88,&H00FFFFFF,&H0000D7FF,&H00000000,&H80000000,-1,0,0,0,100,100,"
        "0,0,1,4,3,5,90,90,0,1\n"
        "Style: HookRim,Segoe UI,64,&HFF000000,&HFF000000,&H0000D7FF,&HB0080808,-1,0,0,0,100,"
        "100,0,0,3,5,0,8,220,220,0,1\n"
        "Style: Hook,Segoe UI,64,&H00FFFFFF,&H0000D7FF,&H00000000,&HB0080808,-1,0,0,0,100,100,"
        "0,0,3,3,0,8,220,220,0,1\n"
        "Style: Sign,Segoe UI,34,&H60FFFFFF,&H0000D7FF,&H60000000,&H00000000,-1,0,0,0,100,100,"
        "3,0,1,1,0,5,90,90,0,1\n"
        "\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        "Dialogue: 0,0:00:00.00,0:00:00.70,Pop,,0,0,0,,"
        "{\\pos(540,1180)\\fscx30\\fscy30\\t(0,70,\\fscx112\\fscy112)"
        "\\t(70,130,\\fscx100\\fscy100)}ATTAQUE {\\1c&H0000D7FF}CARDIAQUE{\\1c&H00FFFFFF}\n"
        "Dialogue: 1,0:00:00.00,0:00:02.80,HookRim,,0,0,0,,"
        "{\\pos(540,250)\\fad(120,180)\\fscx30\\fscy30\\t(0,70,\\fscx112\\fscy112)"
        "\\t(70,130,\\fscx100\\fscy100)}ELLE SORT LE COUTEAU LA\n"
        "Dialogue: 2,0:00:00.00,0:00:02.80,Hook,,0,0,0,,"
        "{\\pos(540,250)\\fad(120,180)\\fscx30\\fscy30\\t(0,70,\\fscx112\\fscy112)"
        "\\t(70,130,\\fscx100\\fscy100)}ELLE SORT LE COUTEAU LA\n"
        "Dialogue: 0,0:00:00.00,0:00:20.00,Sign,,0,0,0,,"
        "{\\pos(540,606)}TWITCH.TV/PSEUDO\n"
    )
    assert apres == golden


def test_ass_tier_haut_grossit_et_blanchit():
    mots = [_w(0.0, "enorme")]
    out = build_pop_ass(mots, STYLE, clip_start_s=0.0, clip_dur_s=5.0,
                         hook=None, handle=None, tiers=["haut"])
    assert "\\fs" in out and "&HFFFFFF&" in out


def test_ass_tier_bas_retrecit_et_grise():
    mots = [_w(0.0, "chuchote")]
    out = build_pop_ass(mots, STYLE, clip_start_s=0.0, clip_dur_s=5.0,
                         hook=None, handle=None, tiers=["bas"])
    ligne = next(l for l in out.splitlines() if ",Pop," in l)
    assert f"\\fs{round(0.85 * STYLE.font_size)}" in ligne and "&HD8D8D8&" in ligne


def test_tier_haut_sur_mot_cle_garde_la_couleur_highlight():
    # seul le mot-cle (le plus long, ici "cardiaque") porte "haut" -- si le tag
    # de taille venait du mot non-cle (ancienne fixture ["haut","haut"]), une
    # implementation qui sauterait les tags de tier sur le mot-cle passerait
    # quand meme ; ici le fs de tier ne peut venir que du mot-cle lui-meme.
    mots = [_w(10.0, "attaque"), _w(10.4, "cardiaque")]
    out = build_pop_ass(mots, STYLE, clip_start_s=10.0, clip_dur_s=20.0,
                         hook=None, handle=None, tiers=["normal", "haut"])
    ligne = next(l for l in out.splitlines() if ",Pop," in l)
    fs_haut = f"\\fs{round(1.35 * STYLE.font_size)}"
    assert fs_haut in ligne
    assert hex_to_ass(STYLE.highlight_hex) in ligne


def test_les_tiers_reels_de_word_tiers_donnent_les_deux_emphases():
    """Revue finale V6 : un tier inconnu etait avale sans un mot ; renommer
    "haut" d'un seul cote (emphasis.py ou ici) effacait l'emphase de tous les
    clips, suites vertes. La sortie REELLE de word_tiers sur une serie
    contrastee passe ici telle quelle."""
    mots = [_w(0.0, "chut", dur=0.5), _w(1.0, "bon", dur=0.5), _w(2.0, "alors", dur=0.5),
            _w(3.0, "enorme", dur=0.5)]
    tiers = word_tiers(mots, [(0.25, -50.0), (1.25, -30.0), (2.25, -28.0), (3.25, -8.0)])
    assert len(set(tiers) - {"normal"}) == 2, tiers
    out = build_pop_ass(mots, STYLE, clip_start_s=0.0, clip_dur_s=5.0, hook=None, handle=None,
                        tiers=tiers)
    assert f"\\fs{round(1.35 * STYLE.font_size)}" in out
    assert f"\\fs{round(0.85 * STYLE.font_size)}" in out


def test_un_tier_inconnu_leve_une_erreur_qui_le_nomme():
    with pytest.raises(ValueError, match="'fort'"):
        build_pop_ass([_w(0.0, "go")], STYLE, clip_start_s=0.0, clip_dur_s=5.0,
                      hook=None, handle=None, tiers=["fort"])


def test_tiers_desalignes_leve_une_erreur():
    # ValueError (pas AssertionError) : survit a python -O et porte un message
    # exploitable au lieu du str() vide d'AssertionError() (cf. queue.py qui
    # stocke str(exc) en base sur toute exception non geree du rendu).
    with pytest.raises(ValueError, match="desalignes"):
        build_pop_ass([_w(0.0, "go"), _w(0.4, "go")], STYLE, clip_start_s=0.0,
                       clip_dur_s=5.0, hook=None, handle=None, tiers=["haut"])


def test_group_words_zippe_les_tiers_avant_le_filtre_vide():
    mots = [_w(0.0, "il"), _w(0.35, ""), _w(0.5, "va")]
    gs = group_words(mots, tiers=["bas", "normal", "haut"])
    assert [g.words for g in gs] == [["il", "va"]]
    assert gs[0].tiers == ["bas", "haut"]


def test_group_words_sans_tiers_remplit_normal():
    gs = group_words([_w(0.0, "go")])
    assert gs[0].tiers == ["normal"]


def test_une_coupe_entre_deux_mots_proches_ouvre_un_nouveau_groupe():
    """Revue finale V6 (lot C, C1) : une coupe de montage supprime la pause qui
    separait deux phrases ; en sortie, l'ecart tombe sous max_gap_s et les deux
    mots fusionnaient en une ligne a cheval sur la coupe."""
    mots = [_w(0.0, "touche", dur=0.4), _w(0.5, "non", dur=0.3)]
    assert [g.words for g in group_words(mots)] == [["touche", "non"]]
    groupes = group_words(mots, cut_times=[0.45])
    assert [g.words for g in groupes] == [["touche"], ["non"]]
    assert groupes[0].end <= 0.45 <= groupes[1].start


def test_une_coupe_posee_pile_sur_les_bords_des_mots_separe_encore():
    """Les copies de mots de TimeMap sont clampees au segment : le dernier mot
    avant une coupe peut finir PILE sur elle, le premier d'apres y commencer."""
    mots = [Word(start=1.2, end=1.8, text="plus"), Word(start=1.8, end=2.1, text="tard")]
    assert [g.words for g in group_words(mots, cut_times=[1.8])] == [["plus"], ["tard"]]


def test_des_coupes_hors_des_intervalles_entre_mots_ne_changent_rien():
    mots = [_w(0.0, "il", dur=0.3), _w(0.35, "manque", dur=0.3), _w(0.72, "de", dur=0.3),
            _w(1.1, "mourir", dur=0.3)]
    sans = group_words(mots)
    assert group_words(mots, cut_times=[]) == sans
    assert group_words(mots, cut_times=[0.1, 0.5, 5.0]) == sans


def test_build_pop_ass_transmet_les_coupes_au_groupement():
    mots = [_w(0.0, "chanson", dur=0.4), _w(0.5, "oula", dur=0.3)]
    sans = build_pop_ass(mots, STYLE, clip_start_s=0.0, clip_dur_s=5.0, hook=None, handle=None)
    avec = build_pop_ass(mots, STYLE, clip_start_s=0.0, clip_dur_s=5.0, hook=None, handle=None,
                         cut_times=[0.45])
    assert sans.count(",Pop,") == 1 and avec.count(",Pop,") == 2


# --- Elisions et traits d'union : l'unite d'affichage n'est pas le mot de la base ---


def test_une_elision_forme_un_seul_mot_de_groupe():
    gs = group_words([_w(0.0, "c"), _w(0.32, "'est"), _w(0.64, "pas")])
    assert [g.words for g in gs] == [["c'est", "pas"]]


def test_un_trait_d_union_forme_un_seul_mot_de_groupe():
    gs = group_words([_w(0.0, "allez"), _w(0.32, "-vous")])
    assert [g.words for g in gs] == [["allez-vous"]]


def test_le_plafond_de_mots_ne_coupe_pas_une_elision():
    """Sans l'unite, la coupe a 3 mots tombait entre « c » et « 'est » : la
    premiere ligne finissait par « C » et la suivante ouvrait sur « 'EST »."""
    gs = group_words([_w(0.0, "on"), _w(0.32, "dit"), _w(0.64, "c"),
                      _w(0.96, "'est"), _w(1.28, "ca")])
    assert [g.words for g in gs] == [["on", "dit", "c'est"], ["ca"]]


def test_le_plafond_de_caracteres_ne_coupe_pas_une_elision():
    gs = group_words([_w(0.0, "incroyablement"), _w(0.4, "c", 0.05),
                      _w(0.5, "'est")])
    assert [g.words for g in gs] == [["incroyablement"], ["c'est"]]


def test_une_pause_ne_coupe_pas_une_elision():
    """Meme separees d'une seconde par la transcription, les deux moities
    restent un seul mot a l'ecran."""
    gs = group_words([_w(0.0, "c", 0.2), _w(1.5, "'est")])
    assert [g.words for g in gs] == [["c'est"]]
    assert (gs[0].start, gs[0].end) == (0.0, 1.8)


def test_le_tier_le_plus_fort_gouverne_l_unite():
    """Un seul jeu de tags ASS s'applique a l'unite : rapetisser « c'est » parce
    que le « c » atone tombe sous p25 effacerait l'emphase de la syllabe qui
    porte le sens. Le maximum est le bon agregat d'un signal de saillance."""
    gs = group_words([_w(0.0, "c"), _w(0.32, "'est")], tiers=["bas", "haut"])
    assert gs[0].words == ["c'est"]
    assert gs[0].tiers == ["haut"]


def test_le_tier_normal_l_emporte_sur_bas_dans_une_unite():
    gs = group_words([_w(0.0, "c"), _w(0.32, "'est")], tiers=["normal", "bas"])
    assert gs[0].tiers == ["normal"]


def test_le_mot_cle_se_choisit_sur_les_unites_pas_sur_les_moities():
    """« c'est » (5 signes) doit battre « on » (2), alors que le « c » seul
    perdait contre lui."""
    gs = group_words([_w(0.0, "on"), _w(0.32, "c"), _w(0.64, "'est")])
    assert gs[0].words == ["on", "c'est"]
    assert gs[0].key_index == 1


def test_l_ass_ecrit_l_elision_sans_espace():
    ass = build_pop_ass([_w(10.0, "c"), _w(10.32, "'est"), _w(10.64, "moi")], STYLE,
                        clip_start_s=10.0, clip_dur_s=20.0, hook=None, handle=None)
    assert "C'EST" in ass
    assert "C 'EST" not in ass


def test_une_coupe_entre_deux_unites_ouvre_encore_un_groupe():
    """Les deux regles se composent : la coupe de montage est jugee entre unites
    d'affichage, donc « c'est » reste entier et « pas » ouvre le groupe suivant."""
    mots = [_w(0.0, "c", 0.2), _w(0.2, "'est", 0.2), _w(0.5, "pas", 0.3)]
    assert [g.words for g in group_words(mots)] == [["c'est", "pas"]]
    assert [g.words for g in group_words(mots, cut_times=[0.45])] == [["c'est"], ["pas"]]


def test_une_coupe_entre_les_moities_d_une_elision_ne_la_separe_pas():
    """Les moities d'une elision ne sont jamais deux mots a l'ecran : une coupe
    posee entre elles ne decide rien, elle n'est jugee qu'entre unites."""
    mots = [_w(0.0, "c", 0.2), _w(0.5, "'est", 0.2)]
    assert [g.words for g in group_words(mots, cut_times=[0.35])] == [["c'est"]]
