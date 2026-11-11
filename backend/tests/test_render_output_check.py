"""Le controle du fichier rendu (render.output_check, appele par
render_clip_job) : duree planifiee contre duree de chaque flux, tolerance,
source plus courte que son conteneur, panne de sonde, rendu refuse mis de cote
sous un nom qui dit l'essai, .part orphelin.

Decoupe de test_render_queue.py (revue finale V6, lot D), dont viennent les
helpers. ffmpeg entierement simule (fixture `ffmpeg` de conftest.py).
"""
from pathlib import Path

import pytest
from test_render_queue import _clip, _rendre, _seed

from clipforge import db as dbmod
from clipforge.render import livraison
from clipforge.render import naming as naming_mod


def test_un_rendu_qui_n_ecrit_rien_finit_en_erreur(tmp_path, ffmpeg):
    """Revue finale V6 (lot C, C3) : rc=0 suffisait a passer le clip en 'done'."""
    ffmpeg.ecrit = False
    clip_id = _seed(tmp_path / "clipforge.db")
    _rendre(tmp_path, clip_id)
    clip = _clip(tmp_path, clip_id)
    assert clip["status"] == "error" and "aucune sortie" in clip["error"], clip["error"]
    assert len(ffmpeg.calls) == 2      # nvenc puis cpu, chacun refuse


def test_un_second_rendu_muet_ne_valide_pas_le_fichier_du_premier(tmp_path, ffmpeg):
    """Une retouche vise le MEME nom de livrable : un rendu qui n'ecrit rien ne
    doit pas passer sur la foi du fichier precedent, qui reste en place."""
    clip_id = _seed(tmp_path / "clipforge.db")
    _rendre(tmp_path, clip_id)
    premier = Path(_clip(tmp_path, clip_id)["output_path"])
    ffmpeg.ecrit = False
    _rendre(tmp_path, clip_id)
    clip = _clip(tmp_path, clip_id)
    assert clip["status"] == "error" and "aucune sortie" in clip["error"], clip["error"]
    assert premier.is_file()


def test_une_sortie_tronquee_finit_en_erreur_avec_les_deux_durees(tmp_path, ffmpeg):
    ffmpeg.durees = [7.0]
    clip_id = _seed(tmp_path / "clipforge.db")
    _rendre(tmp_path, clip_id)
    clip = _clip(tmp_path, clip_id)
    assert clip["status"] == "error", clip["error"]
    assert "7.000" in clip["error"] and "10.000" in clip["error"], clip["error"]
    assert len(ffmpeg.calls) == 2


def test_une_video_plus_courte_que_son_conteneur_finit_en_erreur(tmp_path, ffmpeg):
    """Limite residuelle du lot A2 : un morceau vide dans un trou video raccourcit
    la seule video (mesure : 9,0 s de video, 9,6 s d'audio et de conteneur)."""
    ffmpeg.manque_video = 0.6
    clip_id = _seed(tmp_path / "clipforge.db")
    _rendre(tmp_path, clip_id)
    clip = _clip(tmp_path, clip_id)
    assert clip["status"] == "error" and "video" in clip["error"], clip["error"]
    assert "9.400" in clip["error"], clip["error"]


def test_une_sortie_nvenc_tronquee_laisse_sa_chance_au_cpu(tmp_path, ffmpeg, caplog):
    ffmpeg.durees = [7.0, None]
    clip_id = _seed(tmp_path / "clipforge.db")
    with caplog.at_level("WARNING"):
        _rendre(tmp_path, clip_id)
    assert _clip(tmp_path, clip_id)["status"] == "done"
    assert len(ffmpeg.calls) == 2 and "libx264" in ffmpeg.calls[1]
    assert "7.000" in caplog.text, caplog.text


@pytest.mark.parametrize("ecart", [-0.2, 0.0, 0.14, 0.2])
def test_une_sortie_juste_a_la_tolerance_pres_est_validee(tmp_path, ffmpeg, ecart):
    """Ecarts mesures sur des rendus sains : +0,000 a +0,070 s sur les vrais clips
    (1080p60), jusqu'a +0,140 s en integration (une image a 5 ips)."""
    ffmpeg.durees = [10.0 + ecart]
    clip_id = _seed(tmp_path / "clipforge.db")
    _rendre(tmp_path, clip_id)
    assert _clip(tmp_path, clip_id)["status"] == "done", _clip(tmp_path, clip_id)["error"]


def _sorties(tmp_path: Path) -> list[str]:
    return sorted(p.name for p in (tmp_path / "output").iterdir())


def _refus(tmp_path: Path, clip_id: int) -> list[str]:
    return sorted(p.name for p in (tmp_path / "jobs" / f"clip{clip_id}").glob("refus-*"))


def test_un_rendu_refuse_ne_laisse_aucun_fichier_dans_le_dossier_de_sortie(tmp_path, ffmpeg):
    """Revue finale V6 (fix round 1, R1) : ffmpeg ecrivait au nom du livrable, et
    un rendu refuse restait visible comme s'il etait le clip livre. Mesure sur
    rendus reels : trois refus de suite laissaient « refus [c1].mp4 »,
    « (2) », « (3) », qu'aucune ligne de la base ne suivait."""
    ffmpeg.durees = [7.0]
    clip_id = _seed(tmp_path / "clipforge.db")
    _rendre(tmp_path, clip_id)
    clip = _clip(tmp_path, clip_id)
    assert clip["status"] == "error", clip["error"]
    assert _sorties(tmp_path) == [], _sorties(tmp_path)
    assert _refus(tmp_path, clip_id) == ["refus-cpu.mp4", "refus-nvenc.mp4"]


def test_un_rendu_accepte_prend_son_nom_final_sans_part_residuel(tmp_path, ffmpeg):
    clip_id = _seed(tmp_path / "clipforge.db")
    _rendre(tmp_path, clip_id)
    clip = _clip(tmp_path, clip_id)
    assert clip["status"] == "done", clip["error"]
    assert _sorties(tmp_path) == [Path(clip["output_path"]).name]
    assert ".part" not in _sorties(tmp_path)[0]
    assert _refus(tmp_path, clip_id) == []


def test_un_rendu_refuse_laisse_le_bon_rendu_precedent_intact(tmp_path, ffmpeg):
    """Une retouche refusee ecrasait le bon rendu precedent (mesure reelle :
    13 243 492 octets remplaces par 5 413 151)."""
    clip_id = _seed(tmp_path / "clipforge.db")
    _rendre(tmp_path, clip_id)
    bon = Path(_clip(tmp_path, clip_id)["output_path"])
    octets = bon.read_bytes()

    ffmpeg.durees = [7.0]
    ffmpeg.rendus = 0
    _rendre(tmp_path, clip_id)
    assert _clip(tmp_path, clip_id)["status"] == "error"
    assert bon.read_bytes() == octets
    assert _sorties(tmp_path) == [bon.name]


def test_un_part_orphelin_n_est_jamais_promu_au_nom_du_livrable(tmp_path, ffmpeg):
    """Un .part laisse par un process interrompu ne doit pas passer pour le
    rendu de la tentative suivante : il est mis de cote avant d'ecrire."""
    clip_id = _seed(tmp_path / "clipforge.db")
    conn = dbmod.get_db(tmp_path / "clipforge.db")
    try:
        clip = conn.execute("SELECT * FROM clips WHERE id=?", (clip_id,)).fetchone()
        out = naming_mod.output_target(conn, clip, clip_id, str(tmp_path / "output"))
    finally:
        conn.close()
    orphelin = livraison.part_path(out.parent, clip_id)
    orphelin.write_text("mp4 10.0", encoding="utf-8")

    ffmpeg.ecrit = False
    _rendre(tmp_path, clip_id)
    assert _clip(tmp_path, clip_id)["status"] == "error"
    assert not out.is_file() and not orphelin.is_file()
    job = tmp_path / "jobs" / f"clip{clip_id}"
    assert sorted(p.name for p in job.glob("*.mp4")) == ["orphelin.mp4"]


@pytest.mark.parametrize("panne, extrait", [("absente", "introuvable"),
                                            ("expire", "rendu la main"),
                                            ("illisible", "illisible")])
def test_une_panne_de_sonde_ne_relance_pas_le_rendu_et_n_accuse_pas_ffmpeg(
        tmp_path, ffmpeg, panne, extrait):
    """Revue finale V6 (fix round 1, R3) : ffprobe absent ou en panne repartait
    pour un reencodage CPU complet et finissait par « ffmpeg a echoue », ce qui
    est faux. Une panne de SONDE ne se repare pas en rendant une seconde fois."""
    ffmpeg.sonde_panne = panne
    clip_id = _seed(tmp_path / "clipforge.db")
    _rendre(tmp_path, clip_id)
    clip = _clip(tmp_path, clip_id)
    assert clip["status"] == "error"
    assert "ffmpeg a echoue" not in clip["error"], clip["error"]
    assert "ffprobe" in clip["error"] and extrait in clip["error"], clip["error"]
    assert len(ffmpeg.calls) == 1, ffmpeg.calls
    assert _sorties(tmp_path) == []
    job = tmp_path / "jobs" / f"clip{clip_id}"
    # Fix round 2 (K4) : un rendu que la sonde n'a pas pu juger n'est pas un refus.
    assert sorted(p.name for p in job.glob("*.mp4")) == ["non-controle-nvenc.mp4"]


def test_un_clip_qui_touche_la_fin_d_une_video_plus_courte_que_son_conteneur(tmp_path, ffmpeg):
    """Revue finale V6 (fix round 1, R2) : les fins d'EDL sont bornees a
    vods.duration_s, qui vient du CONTENEUR. Sur le dernier clip d'une VOD dont
    le flux video s'arrete avant, un rendu sain sortait plus court que planifie
    et etait rejete (mesure sur source fabriquee : video 8,800 s, audio et
    conteneur 10,000 s, pour 10,000 s planifiees)."""
    ffmpeg.duree_source = 15.0
    ffmpeg.fin_video_source = 13.8
    ffmpeg.manque_video = 1.2
    clip_id = _seed(tmp_path / "clipforge.db")
    _rendre(tmp_path, clip_id)
    clip = _clip(tmp_path, clip_id)
    assert clip["status"] == "done", clip["error"]
    assert len(ffmpeg.calls) == 1


def test_une_video_courte_sans_source_courte_reste_une_erreur(tmp_path, ffmpeg):
    """Le plafond ne doit pas pardonner une video courte quand la source, elle,
    va jusqu'au bout : c'est le cas du morceau vide dans un trou video."""
    ffmpeg.manque_video = 1.2
    clip_id = _seed(tmp_path / "clipforge.db")
    _rendre(tmp_path, clip_id)
    clip = _clip(tmp_path, clip_id)
    assert clip["status"] == "error" and "video" in clip["error"], clip["error"]


def test_une_piste_audio_courte_dans_la_sortie_finit_en_erreur(tmp_path, ffmpeg):
    """Revue finale V6 (fix round 1, R4) : le relecteur a produit une sortie de
    20 s dont la piste audio ne faisait que 12 s, statut 'done'."""
    ffmpeg.manque_audio = 4.0
    clip_id = _seed(tmp_path / "clipforge.db")
    _rendre(tmp_path, clip_id)
    clip = _clip(tmp_path, clip_id)
    assert clip["status"] == "error" and "audio" in clip["error"], clip["error"]
    assert "6.000" in clip["error"] and "10.000" in clip["error"], clip["error"]


def test_un_clip_qui_touche_la_fin_d_un_audio_plus_court_que_son_conteneur(tmp_path, ffmpeg):
    """Meme borne que R2, cote audio : une source dont la piste audio s'arrete
    avant son conteneur rend un clip sain plus court de ce qui manque."""
    ffmpeg.duree_source = 15.0
    ffmpeg.fin_audio_source = 14.2
    ffmpeg.manque_audio = 0.8
    clip_id = _seed(tmp_path / "clipforge.db")
    _rendre(tmp_path, clip_id)
    assert _clip(tmp_path, clip_id)["status"] == "done", _clip(tmp_path, clip_id)["error"]


@pytest.mark.parametrize("ecart_audio", [-0.07, 0.0, 0.02])
def test_une_piste_audio_juste_a_la_tolerance_pres_est_validee(tmp_path, ffmpeg, ecart_audio):
    """Ecarts audio mesures contre la duree planifiee : +0,000 a +0,070 s sur
    les 6 rendus reels, +0,000 a +0,020 s sur les 15 rendus d'integration."""
    ffmpeg.manque_audio = ecart_audio
    clip_id = _seed(tmp_path / "clipforge.db")
    _rendre(tmp_path, clip_id)
    assert _clip(tmp_path, clip_id)["status"] == "done", _clip(tmp_path, clip_id)["error"]


def test_une_sortie_sans_piste_audio_passe_mais_le_dit(tmp_path, ffmpeg, caplog):
    """Le V5 mappe 0:a? : une source sans audio rend legitimement une video
    seule. Ce qui n'est pas controle doit se voir dans le journal."""
    ffmpeg.source_sans_audio = True
    ffmpeg.sans_audio = True
    clip_id = _seed(tmp_path / "clipforge.db")
    with caplog.at_level("WARNING"):
        _rendre(tmp_path, clip_id)
    assert _clip(tmp_path, clip_id)["status"] == "done", _clip(tmp_path, clip_id)["error"]
    assert "audio" in caplog.text.lower(), caplog.text


def test_une_sortie_entiere_malgre_une_source_courte_reste_acceptee(tmp_path, ffmpeg):
    """Ce que la source n'a pas ne se perd que si ca tombe dans une zone gardee.
    Mesure sur le rendu d'integration a 200 segments : la video de la source
    s'arrete 0,14 s avant la fin du clip et le rendu sort quand meme sa duree
    planifiee entiere. La duree attendue est donc un PLANCHER, pas une cible
    decalee -- sans quoi ce rendu sain part en erreur."""
    ffmpeg.duree_source = 15.0
    ffmpeg.fin_video_source = 13.8
    clip_id = _seed(tmp_path / "clipforge.db")
    _rendre(tmp_path, clip_id)
    clip = _clip(tmp_path, clip_id)
    assert clip["status"] == "done", clip["error"]


def test_un_refus_de_duree_ne_dit_pas_que_ffmpeg_a_echoue(tmp_path, ffmpeg):
    """Fix round 2 (K4) : deux rendus sortis en rc=0 puis refuses au controle
    finissaient en « ffmpeg a echoue (nvenc puis cpu) », ce qui est faux."""
    ffmpeg.durees = [7.0]
    clip_id = _seed(tmp_path / "clipforge.db")
    _rendre(tmp_path, clip_id)
    erreur = _clip(tmp_path, clip_id)["error"]
    assert erreur.startswith("sortie refusee (nvenc puis cpu) : "), erreur
    assert "ffmpeg a echoue" not in erreur, erreur


def test_une_sortie_muette_d_une_source_sonore_est_refusee(tmp_path, ffmpeg):
    """Fix round 2 (K4) : un clip muet partait en 'done' avec un simple
    avertissement, meme quand la source avait une piste audio (mesure du
    relecteur : sortie sans audio, statut 'done')."""
    ffmpeg.sans_audio = True
    clip_id = _seed(tmp_path / "clipforge.db")
    _rendre(tmp_path, clip_id)
    clip = _clip(tmp_path, clip_id)
    assert clip["status"] == "error" and "aucune piste audio" in clip["error"], clip["error"]


def test_une_fenetre_qui_deborde_le_fichier_n_abaisse_pas_le_plancher(tmp_path, ffmpeg, caplog):
    """Fix round 2 (K2) : quand la fenetre du clip depasse la fin reelle du
    FICHIER (vods.duration_s a 40 pour un fichier de 30 s), le plancher
    descendait de tout ce qui manque, en silence. Mesure : un clip V5 de 0,5 s
    pour 10 s planifiees en 'done', un V6 tronque de 3 s en 'done'. Ici le
    fichier finit a 10,5 s, le clip [5 ; 15] sort de 5,5 s : refuse."""
    ffmpeg.duree_source = 10.5
    ffmpeg.durees = [5.5]
    clip_id = _seed(tmp_path / "clipforge.db")
    with caplog.at_level("WARNING"):
        _rendre(tmp_path, clip_id)
    clip = _clip(tmp_path, clip_id)
    assert clip["status"] == "error" and "5.500" in clip["error"], clip["error"]
    assert "10.000 s planifiees" in clip["error"], clip["error"]
    assert "avant la fin du clip" not in caplog.text, caplog.text
    # Fix round 3 (F3) : ce debordement se dit, dans le journal et dans l'erreur
    # (cas reel de la VOD 2 : 9644,16 s en base, 9580,07 s de fichier).
    assert "la duree enregistree en base depasse celle du fichier" in caplog.text, caplog.text
    attendu = "le fichier s'arrete a 10.500 s, avant la fenetre demandee (15.000 s)"
    assert attendu in clip["error"], clip["error"]


def test_un_plancher_qui_descend_le_dit_de_combien_et_pourquoi(tmp_path, ffmpeg, caplog):
    ffmpeg.duree_source = 15.0
    ffmpeg.fin_video_source = 13.8
    ffmpeg.manque_video = 1.2
    clip_id = _seed(tmp_path / "clipforge.db")
    with caplog.at_level("WARNING"):
        _rendre(tmp_path, clip_id)
    assert _clip(tmp_path, clip_id)["status"] == "done"
    assert "flux video fini 1.200 s avant la fin du clip (15.000 s)" in caplog.text, caplog.text
    assert "accepte des 8.800 s au lieu de 10.000 s" in caplog.text, caplog.text


@pytest.mark.parametrize("fin_video, video_rendue, statut", [(9.0, 4.0, "error"),
                                                              (10.0, 5.0, "done")])
def test_un_clip_dont_plus_de_la_moitie_manque_n_est_pas_livre(
        tmp_path, ffmpeg, fin_video, video_rendue, statut):
    """Fix round 2 (K2) : la source ne peut pas donner plus, mais un clip dont
    plus de la moitie manque n'est pas un clip. Chiffre du relecteur : clip
    [5 ; 15], video jusqu'a 10 s, une sortie de 5,0 s passe (la moitie pile) ;
    video jusqu'a 9 s, 4,0 s rendues, refuse."""
    ffmpeg.duree_source = 15.0
    ffmpeg.fin_video_source = fin_video
    ffmpeg.manque_video = 10.0 - video_rendue
    clip_id = _seed(tmp_path / "clipforge.db")
    _rendre(tmp_path, clip_id)
    clip = _clip(tmp_path, clip_id)
    assert clip["status"] == statut, clip["error"]


def test_la_fin_d_un_audio_decale_compte_son_debut(tmp_path, ffmpeg):
    """Fix round 2 (K4) : la fin d'un flux est start_time + duration. Audio de
    2,98 a 15 s : lu comme finissant a 12,02 s, il pardonnait 2,98 s de
    troncature de l'audio (mesure du relecteur : 2,5 s tronquees, 'done')."""
    ffmpeg.duree_source = 15.0
    ffmpeg.debut_audio_source = 2.98
    ffmpeg.manque_audio = 2.5
    clip_id = _seed(tmp_path / "clipforge.db")
    _rendre(tmp_path, clip_id)
    clip = _clip(tmp_path, clip_id)
    assert clip["status"] == "error" and "duree audio 7.500" in clip["error"], clip["error"]


def test_un_conteneur_court_est_refuse_meme_quand_les_flux_sont_entiers(tmp_path, ffmpeg):
    """Trou de mutation (fix round 2, K4) : le plancher du conteneur mis a 0
    laissait passer ce rendu."""
    ffmpeg.durees = [7.0]
    ffmpeg.manque_video = ffmpeg.manque_audio = -3.0
    clip_id = _seed(tmp_path / "clipforge.db")
    _rendre(tmp_path, clip_id)
    clip = _clip(tmp_path, clip_id)
    assert clip["status"] == "error" and "duree conteneur 7.000" in clip["error"], clip["error"]


def test_une_video_de_duree_inconnue_est_refusee(tmp_path, ffmpeg):
    ffmpeg.video_sans_duree = True
    clip_id = _seed(tmp_path / "clipforge.db")
    _rendre(tmp_path, clip_id)
    clip = _clip(tmp_path, clip_id)
    assert clip["status"] == "error" and "duree video inconnue" in clip["error"], clip["error"]


def test_une_sortie_plus_longue_que_prevu_est_refusee(tmp_path, ffmpeg):
    """Le plafond compte autant que le plancher : 10,5 s pour 10 s planifiees
    n'est pas un rendu sain (pire ecart mesure : +0,140 s)."""
    ffmpeg.durees = [10.5]
    clip_id = _seed(tmp_path / "clipforge.db")
    _rendre(tmp_path, clip_id)
    clip = _clip(tmp_path, clip_id)
    assert clip["status"] == "error" and "10.500" in clip["error"], clip["error"]


@pytest.mark.parametrize("video_rendue, statut", [(12.533, "done"), (11.9, "error")])
def test_la_fin_d_un_flux_se_compte_depuis_le_debut_du_fichier(
        tmp_path, ffmpeg, video_rendue, statut):
    """Fix round 3 (F2), decale_video_courte.mp4 du relecteur : conteneur qui
    demarre a 1,479 s, video de 1,5 a 28,0 s, audio sur tout le fichier. -ss
    compte depuis le debut du fichier : la video finit a 26,521 s. V5
    [14 ; 30] rend 12,533 s de video (mesure) ; le plancher ne comptait que
    2,0 s manquantes au lieu de 3,48 et refusait ce rendu sain."""
    ffmpeg.debut_source, ffmpeg.duree_source = 1.478667, 30.021333
    ffmpeg.debut_video_source, ffmpeg.fin_video_source = 1.5, 28.0
    ffmpeg.debut_audio_source = 1.478667
    ffmpeg.manque_video = 16.0 - video_rendue
    db_path = tmp_path / "clipforge.db"
    clip_id = _seed(db_path)
    conn = dbmod.get_db(db_path)
    try:
        conn.execute("UPDATE clips SET start_s=14.0, end_s=30.0 WHERE id=?", (clip_id,))
        conn.commit()
    finally:
        conn.close()
    _rendre(tmp_path, clip_id)
    clip = _clip(tmp_path, clip_id)
    assert clip["status"] == statut, clip["error"]


def test_une_sortie_muette_d_une_source_mkv_est_refusee(tmp_path, ffmpeg):
    """Fix round 3 (F3) : ffprobe ne donne pas de duree par flux pour un MKV
    (format par defaut d'OBS, accepte a l'import). La presence d'une piste audio
    etait lue sur sa duree : une sortie muette passait avec « source sans
    audio »."""
    ffmpeg.source_sans_duree_par_flux = True
    ffmpeg.sans_audio = True
    clip_id = _seed(tmp_path / "clipforge.db")
    _rendre(tmp_path, clip_id)
    clip = _clip(tmp_path, clip_id)
    assert clip["status"] == "error" and "aucune piste audio" in clip["error"], clip["error"]


def test_une_source_mkv_sans_duree_par_flux_ne_borne_rien(tmp_path, ffmpeg):
    """Le revers : sans duree par flux, aucun plancher ne descend et un rendu
    sain passe (la source ne dit pas ou ses flux s'arretent)."""
    ffmpeg.source_sans_duree_par_flux = True
    clip_id = _seed(tmp_path / "clipforge.db")
    _rendre(tmp_path, clip_id)
    assert _clip(tmp_path, clip_id)["status"] == "done"


def test_une_fenetre_dans_le_fichier_ne_parle_pas_de_debordement(tmp_path, ffmpeg, caplog):
    """Le revers du F3 : une fenetre qui tient dans le fichier ne dit rien et
    n'ajoute rien au message d'un refus."""
    ffmpeg.duree_source = 15.0
    ffmpeg.durees = [5.5]
    clip_id = _seed(tmp_path / "clipforge.db")
    with caplog.at_level("WARNING"):
        _rendre(tmp_path, clip_id)
    clip = _clip(tmp_path, clip_id)
    assert clip["status"] == "error" and "s'arrete a" not in clip["error"], clip["error"]
    assert "depasse celle du fichier" not in caplog.text, caplog.text


def test_un_plancher_qui_descend_de_moins_d_une_seconde_le_dit_aussi(tmp_path, ffmpeg, caplog):
    """Mutant survivant (fix round 3, F4) : `manque > 1` laissait un plancher
    descendre en silence tant que ce qui manque tenait sous la seconde. Une
    image a 5 ips vaut 0,2 s, un morceau vide au moins 0,6 s : c'est l'ordre de
    grandeur normal d'un manque, pas l'exception."""
    ffmpeg.duree_source = 15.0
    ffmpeg.fin_video_source = 14.5
    ffmpeg.manque_video = 0.5
    clip_id = _seed(tmp_path / "clipforge.db")
    with caplog.at_level("WARNING"):
        _rendre(tmp_path, clip_id)
    assert _clip(tmp_path, clip_id)["status"] == "done"
    assert "flux video fini 0.500 s avant la fin du clip" in caplog.text, caplog.text
    assert "accepte des 9.500 s au lieu de 10.000 s" in caplog.text, caplog.text


def test_une_sortie_dont_l_audio_n_a_pas_de_duree_par_flux_est_acceptee(tmp_path, ffmpeg):
    """Re-revue du fix round 3 : le cote SOURCE juge desormais la PRESENCE du
    flux audio, le cote SORTIE deduisait encore « pas de piste audio » de
    l'absence de duree par flux -- la premisse que ce round venait de corriger,
    laissee en place d'un cote. Une sortie dont ffprobe ne donne pas la duree par
    flux (MKV, WebM) partait en erreur « aucune piste audio dans la sortie »."""
    ffmpeg.sortie_audio_sans_duree = True
    clip_id = _seed(tmp_path / "clipforge.db")
    _rendre(tmp_path, clip_id)
    clip = _clip(tmp_path, clip_id)
    assert clip["status"] == "done", clip["error"]
