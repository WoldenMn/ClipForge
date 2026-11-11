"""La livraison du rendu (render.livraison, appele par render_clip_job) : le
temporaire propre a chaque clip, la promotion qui ne prend jamais le livrable
d'un autre clip, et ce qui arrive quand Windows refuse de renommer.

Revue finale V6, fix round 2 (K1, K3, K4). Les comportements de Windows sur
lesquels le module s'appuie (renommage refuse d'un fichier tenu ouvert, os.rename
qui n'ecrase pas) ont des tests reels, sautes ailleurs : la CI tourne sous Linux,
ou la logique est tenue par des pannes simulees.
"""
import errno
import os
import sys
from pathlib import Path

import pytest
from test_render_queue import _clip, _clone_de_clip, _rendre, _seed

from clipforge import db as dbmod
from clipforge.render import livraison
from clipforge.render import naming as naming_mod
from clipforge.render.verrou import temporaire_note, verrou_du_clip

windows = pytest.mark.skipif(sys.platform != "win32",
                             reason="verrous de fichiers propres a Windows")
casse_repliee = pytest.mark.skipif(os.path.normcase("A") != "a",
                                   reason="systeme de fichiers sensible a la casse")


def _sorties(tmp_path: Path) -> list[str]:
    return sorted(p.name for p in (tmp_path / "output").iterdir())


def _mis_de_cote(tmp_path: Path, clip_id: int) -> list[str]:
    job = tmp_path / "jobs" / f"clip{clip_id}"
    return sorted(p.name for p in job.glob("*.mp4")) if job.is_dir() else []


def _temporaire(tmp_path: Path, clip_id: int, dossier: str = "output") -> Path:
    return livraison.part_path(tmp_path / dossier, clip_id)


def test_deux_clips_homonymes_rendus_de_front_livrent_deux_fichiers(tmp_path, ffmpeg):
    """K1 : deux clips auto au meme titre visaient le MEME .part et le MEME
    livrable. Le pool en rend deux de front : le second deplacait le .part du
    premier en pleine ecriture (mesure : « aucune sortie ecrite », son rendu
    dans jobs/clip2/refus-orphelin.mp4), ou promouvait par-dessus lui (deux
    lignes 'done' pour un fichier). Ici le clip 2 est rendu en entier PENDANT
    le rendu du clip 1, apres que ffmpeg a ecrit le .part de 1."""
    premier = _seed(tmp_path / "clipforge.db")
    second = _clone_de_clip(tmp_path / "clipforge.db", premier)
    ffmpeg.pendant_rendu = lambda: _rendre(tmp_path, second)
    _rendre(tmp_path, premier)

    lignes = [_clip(tmp_path, c) for c in (premier, second)]
    assert [c["status"] for c in lignes] == ["done", "done"], [c["error"] for c in lignes]
    chemins = [Path(c["output_path"]) for c in lignes]
    assert chemins[0] != chemins[1] and all(p.is_file() for p in chemins), chemins
    assert chemins[1].name.endswith(" - mon clip.mp4"), chemins
    assert chemins[0].name.endswith(" - mon clip (2).mp4"), chemins
    assert _sorties(tmp_path) == sorted(p.name for p in chemins)
    assert _mis_de_cote(tmp_path, premier) == [] and _mis_de_cote(tmp_path, second) == []


def test_le_temporaire_porte_l_id_du_clip_et_pas_son_titre(tmp_path):
    assert livraison.part_path(tmp_path, 7) != livraison.part_path(tmp_path, 8)
    assert "c7" in livraison.part_path(tmp_path, 7).name
    assert livraison.part_path(tmp_path, 7).suffix == ".mp4"


def test_la_promotion_prend_un_rang_plutot_que_le_livrable_d_un_autre_clip(tmp_path):
    vise = tmp_path / "titre.mp4"
    vise.write_bytes(b"livrable d'un autre clip")
    rendu = tmp_path / "rendu.part.mp4"
    rendu.write_bytes(b"mon rendu")

    pris = livraison.promouvoir(rendu, vise, deja_sien=None)

    assert pris == tmp_path / "titre (2).mp4" and pris.read_bytes() == b"mon rendu"
    assert vise.read_bytes() == b"livrable d'un autre clip"


def test_une_retouche_remplace_son_propre_livrable(tmp_path):
    vise = tmp_path / "titre.mp4"
    vise.write_bytes(b"autre clip")
    sien = tmp_path / "titre (2).mp4"
    sien.write_bytes(b"ancien rendu de ce clip")
    rendu = tmp_path / "rendu.part.mp4"
    rendu.write_bytes(b"retouche")

    assert livraison.promouvoir(rendu, vise, deja_sien=sien) == sien
    assert sien.read_bytes() == b"retouche" and vise.read_bytes() == b"autre clip"


def test_le_renommage_sans_ecrasement_hors_windows_n_ecrase_rien(tmp_path):
    """La CI tourne sous Linux, ou os.rename ecraserait la cible sans rien dire."""
    cible = tmp_path / "pris.mp4"
    cible.write_bytes(b"a un autre")
    src = tmp_path / "rendu.part.mp4"
    src.write_bytes(b"mien")
    with pytest.raises(FileExistsError):
        livraison.lier_puis_retirer(src, cible)
    assert cible.read_bytes() == b"a un autre" and src.read_bytes() == b"mien"
    libre = tmp_path / "libre.mp4"
    livraison.lier_puis_retirer(src, libre)
    assert libre.read_bytes() == b"mien" and not src.exists()


@windows
def test_sous_windows_os_rename_n_ecrase_pas_une_cible(tmp_path):
    """La garantie sur laquelle la promotion s'appuie, verifiee sur la machine."""
    cible = tmp_path / "pris.mp4"
    cible.write_bytes(b"a un autre")
    src = tmp_path / "rendu.part.mp4"
    src.write_bytes(b"mien")
    with pytest.raises(FileExistsError):
        os.rename(src, cible)
    assert cible.read_bytes() == b"a un autre"


def _retouche_refusee_par_l_os(tmp_path, ffmpeg, monkeypatch, erreur, code):
    clip_id = _seed(tmp_path / "clipforge.db")
    _rendre(tmp_path, clip_id)
    bon = Path(_clip(tmp_path, clip_id)["output_path"])
    octets = bon.read_bytes()
    reel = os.replace

    def replace(src, dst):
        if Path(dst) == bon:
            raise erreur(code, "refuse par le systeme", str(src), None, str(dst))
        return reel(src, dst)

    monkeypatch.setattr(livraison.os, "replace", replace)
    ffmpeg.durees = [9.9]
    ffmpeg.rendus = 0
    _rendre(tmp_path, clip_id)
    return clip_id, bon, octets


def test_un_livrable_ouvert_ailleurs_laisse_le_rendu_pret_dans_le_job(
        tmp_path, ffmpeg, monkeypatch):
    """K3 : une retouche pendant qu'un lecteur tient le livrable ouvert finissait
    en « [WinError 5] Acces refuse », le bon rendu laisse en .part dans le
    dossier de livraison (ce que R1 interdit), puis range comme orphelin a la
    retouche suivante. Mesure : partages r, rw et rwd, les trois en erreur."""
    clip_id, bon, octets = _retouche_refusee_par_l_os(tmp_path, ffmpeg, monkeypatch,
                                                      PermissionError, errno.EACCES)
    clip = _clip(tmp_path, clip_id)
    assert clip["status"] == "error"
    assert f"{bon.name} est ouvert dans un autre programme" in clip["error"], clip["error"]
    assert "fermez-le puis relancez le rendu" in clip["error"], clip["error"]
    assert bon.read_bytes() == octets
    assert _sorties(tmp_path) == [bon.name]
    assert _mis_de_cote(tmp_path, clip_id) == ["pret-non-promu.mp4"]
    pret = tmp_path / "jobs" / f"clip{clip_id}" / "pret-non-promu.mp4"
    assert pret.read_text(encoding="utf-8") == "mp4 9.9"


def test_une_autre_panne_de_promotion_ne_parle_pas_d_un_lecteur(tmp_path, ffmpeg, monkeypatch):
    clip_id, bon, octets = _retouche_refusee_par_l_os(tmp_path, ffmpeg, monkeypatch,
                                                      OSError, errno.EIO)
    clip = _clip(tmp_path, clip_id)
    assert clip["status"] == "error" and "ouvert dans un autre" not in clip["error"]
    assert f"{bon.name} n'a pas pu etre ecrit" in clip["error"], clip["error"]
    assert bon.read_bytes() == octets
    assert _mis_de_cote(tmp_path, clip_id) == ["pret-non-promu.mp4"]


@windows
def test_un_vrai_lecteur_ouvert_ne_perd_pas_le_rendu(tmp_path, ffmpeg):
    """Meme cas, sans rien simuler : le livrable est tenu ouvert en lecture."""
    clip_id = _seed(tmp_path / "clipforge.db")
    _rendre(tmp_path, clip_id)
    bon = Path(_clip(tmp_path, clip_id)["output_path"])
    octets = bon.read_bytes()
    ffmpeg.durees = [9.9]
    ffmpeg.rendus = 0
    with bon.open("rb"):
        _rendre(tmp_path, clip_id)
    clip = _clip(tmp_path, clip_id)
    assert clip["status"] == "error" and "ouvert dans un autre programme" in clip["error"]
    assert bon.read_bytes() == octets and _sorties(tmp_path) == [bon.name]
    assert _mis_de_cote(tmp_path, clip_id) == ["pret-non-promu.mp4"]


def test_un_refus_qui_ne_peut_pas_etre_mis_de_cote_dit_ou_il_est(tmp_path, monkeypatch):
    """K3, meme traitement pour un echec de mise de cote : le message dit ou le
    fichier est reste, jamais une erreur brute."""
    rendu = tmp_path / "out" / "clipforge-c1.part.mp4"
    rendu.parent.mkdir()
    rendu.write_bytes(b"refuse")

    def rename(src, dst):
        raise PermissionError(13, "Acces refuse", str(src), None, str(dst))

    monkeypatch.setattr(livraison.os, "rename", rename)
    with pytest.raises(RuntimeError, match="n'a pas pu etre mis de cote") as err:
        livraison.mettre_de_cote(rendu, tmp_path / "job", "refus-cpu")
    assert str(rendu.parent) in str(err.value) and rendu.read_bytes() == b"refuse"


def test_un_temporaire_tenu_ouvert_par_un_tiers_n_est_pas_copie(tmp_path, monkeypatch):
    """K4 : shutil.move retombait sur une copie partielle du fichier tenu puis un
    [WinError 32] brut. Un renommage refuse dit maintenant ce qui se passe. Le
    tenant ne peut plus etre un second rendu du meme clip (le verrou de F1 le
    refuse bien avant), donc le message nomme un programme tiers."""
    rendu = tmp_path / "out" / "clipforge-c1.part.mp4"
    rendu.parent.mkdir()
    rendu.write_bytes(b"en cours d'ecriture")

    def rename(src, dst):
        raise PermissionError(13, "fichier utilise par un autre processus", str(src), 32)

    monkeypatch.setattr(livraison.os, "rename", rename)
    with pytest.raises(RuntimeError, match="tenu ouvert par un autre programme"):
        livraison.ecarter_orphelins([rendu], tmp_path / "job", 1)
    assert rendu.read_bytes() == b"en cours d'ecriture"
    assert not (tmp_path / "job").exists() or not any((tmp_path / "job").iterdir())


@windows
def test_un_temporaire_vraiment_tenu_ouvert_n_est_pas_deplace(tmp_path, ffmpeg):
    clip_id = _seed(tmp_path / "clipforge.db")
    rendu = _temporaire(tmp_path, clip_id)
    rendu.parent.mkdir(parents=True, exist_ok=True)
    with rendu.open("ab") as ecrit:
        ecrit.write(b"en cours d'ecriture")
        ecrit.flush()
        _rendre(tmp_path, clip_id)
    clip = _clip(tmp_path, clip_id)
    assert clip["status"] == "error", clip["error"]
    assert "tenu ouvert par un autre programme" in clip["error"], clip["error"]
    assert rendu.read_bytes() == b"en cours d'ecriture"
    assert _mis_de_cote(tmp_path, clip_id) == [] and ffmpeg.calls == []


def test_un_orphelin_est_mis_de_cote_et_journalise(tmp_path, ffmpeg, caplog):
    clip_id = _seed(tmp_path / "clipforge.db")
    orphelin = _temporaire(tmp_path, clip_id)
    orphelin.parent.mkdir(parents=True, exist_ok=True)
    orphelin.write_text("mp4 3.0", encoding="utf-8")
    with caplog.at_level("WARNING"):
        _rendre(tmp_path, clip_id)
    assert _clip(tmp_path, clip_id)["status"] == "done"
    assert _mis_de_cote(tmp_path, clip_id) == ["orphelin.mp4"]
    assert "rendu interrompu" in caplog.text and orphelin.name in caplog.text, caplog.text


def test_l_orphelin_de_l_ancien_dossier_de_sortie_ne_reste_pas(tmp_path, ffmpeg):
    """K4 : un crash laisse le temporaire la ou visait le livrable ; si le
    dossier de sortie change avant la retouche, plus rien ne le ramassait."""
    db_path = tmp_path / "clipforge.db"
    clip_id = _seed(db_path)
    conn = dbmod.get_db(db_path)
    try:
        dbmod.set_setting(conn, "output_dir", str(tmp_path / "ancien"))
        conn.commit()
    finally:
        conn.close()
    _rendre(tmp_path, clip_id)
    assert Path(_clip(tmp_path, clip_id)["output_path"]).parent == tmp_path / "ancien"
    orphelin = _temporaire(tmp_path, clip_id, "ancien")
    orphelin.write_text("mp4 3.0", encoding="utf-8")
    conn = dbmod.get_db(db_path)
    try:
        dbmod.set_setting(conn, "output_dir", str(tmp_path / "nouveau"))
        conn.commit()
    finally:
        conn.close()

    _rendre(tmp_path, clip_id)
    assert _clip(tmp_path, clip_id)["status"] == "done"
    assert not orphelin.exists()
    assert _mis_de_cote(tmp_path, clip_id) == ["orphelin.mp4"]


def test_vers_un_autre_volume_le_refus_est_deplace_une_fois_libre(tmp_path, monkeypatch):
    """Dossier de sortie et jobs sur deux volumes : os.rename leve EXDEV. Le
    temporaire est d'abord renomme sur place (preuve que personne n'ecrit
    dedans), puis deplace ; il arrive entier, sous son nom de mise de cote.

    Ce qui reste dans out/ se juge sur les deux noms que ClipForge y pose, pas
    sur le dossier vide (fix round 3, F5) : sur le poste de dev, un composant
    tiers cree <NOM EN MAJUSCULES>.tmp pendant 0,2 ms apres la suppression de
    tout fichier renomme (20 fois sur 20, meme hors ClipForge), et le test
    rougissait quand la lecture du dossier tombait dedans (3 lancements sur 120)."""
    rendu = tmp_path / "out" / "clipforge-c1.part.mp4"
    rendu.parent.mkdir()
    rendu.write_bytes(b"refuse")
    job = tmp_path / "job"
    reel = os.rename
    vus = []

    def rename(src, dst):
        vus.append(Path(dst).parent)
        if Path(dst).parent == job:
            raise OSError(errno.EXDEV, "autre volume", str(src), None, str(dst))
        return reel(src, dst)

    monkeypatch.setattr(livraison.os, "rename", rename)
    garde = livraison.ecarter(rendu, job, "refus-cpu")
    assert garde == job / "refus-cpu.mp4" and garde.read_bytes() == b"refuse"
    assert not rendu.exists() and not rendu.with_name(rendu.name + ".ecarte").exists()
    assert vus[:2] == [job, rendu.parent], vus


def test_un_rendu_qui_aboutit_efface_le_refus_d_un_double_envoi(tmp_path, ffmpeg):
    """K4, mesure sur deux rendus reels du meme clip : le second, refuse
    (« deja en cours »), ecrivait son erreur sur la ligne que le premier passait
    ensuite en 'done' ; la ligne gardait le message d'un refus deja depasse."""
    clip_id = _seed(tmp_path / "clipforge.db")

    def refus_concurrent():
        conn = dbmod.get_db(tmp_path / "clipforge.db")
        try:
            conn.execute("UPDATE clips SET status='error', error='Un rendu de ce clip est "
                         "deja en cours' WHERE id=?", (clip_id,))
            conn.commit()
        finally:
            conn.close()

    ffmpeg.pendant_rendu = refus_concurrent
    _rendre(tmp_path, clip_id)
    clip = _clip(tmp_path, clip_id)
    assert clip["status"] == "done" and clip["error"] is None, clip["error"]


@pytest.mark.parametrize("nom", ["refus-nvenc.mp4", "refus-cpu (2).mp4", "refus-orphelin.mp4",
                                 "non-controle-cpu.mp4", "orphelin.mp4", "orphelin (3).mp4",
                                 "pret-non-promu.mp4"])
def test_le_motif_reconnait_tout_ce_que_la_livraison_pose(nom):
    """Fix round 3 (F3) : refus-orphelin.mp4, pose par les revisions
    precedentes, n'etait pas reconnu et se faisait annoncer « pas a nous »."""
    assert livraison.MIS_DE_COTE.fullmatch(nom), nom


@pytest.mark.parametrize("nom", ["mes vacances.mp4", "refus.mp4", "refus-cpu.txt",
                                 "orphelin.txt", "clipforge-c1.part.mp4"])
def test_le_motif_ne_reconnait_pas_ce_qui_n_est_pas_a_nous(nom):
    assert not livraison.MIS_DE_COTE.fullmatch(nom), nom


def test_un_temporaire_bloque_par_une_autre_panne_dit_la_panne(tmp_path, monkeypatch):
    """Fix round 3 (F3) : la branche OSError generique d'ecarter_orphelins
    n'etait testee nulle part. Une panne qui n'est pas un verrou ne doit pas
    s'annoncer comme un autre rendu en cours."""
    rendu = tmp_path / "out" / "clipforge-c1.part.mp4"
    rendu.parent.mkdir()
    rendu.write_bytes(b"temporaire")

    def rename(src, dst):
        raise OSError(errno.EIO, "erreur d'entree-sortie", str(src), None, str(dst))

    monkeypatch.setattr(livraison.os, "rename", rename)
    with pytest.raises(RuntimeError, match="n'a pas pu etre mis de cote") as err:
        livraison.ecarter_orphelins([rendu], tmp_path / "job", 1)
    assert "deja en cours" not in str(err.value), str(err.value)
    assert "entree-sortie" in str(err.value) and str(rendu) in str(err.value)
    assert rendu.read_bytes() == b"temporaire"


def test_un_ecarte_laisse_par_un_disque_plein_est_range_au_rendu_suivant(
        tmp_path, ffmpeg, monkeypatch):
    """Fix round 3 (F3) : la copie d'un refus vers le volume des jobs echoue
    (disque plein) ; le temporaire reste dans la livraison sous son nom
    d'attente, ou il restait pour toujours et bloquait les refus suivants du
    clip, sous un message qui conseillait de fermer un programme."""
    clip_id = _seed(tmp_path / "clipforge.db")
    job = tmp_path / "jobs" / f"clip{clip_id}"
    plein = {"disque": True}
    reel = os.rename

    def rename(src, dst):
        if Path(dst).parent == job:
            raise OSError(errno.EXDEV, "autre volume", str(src), None, str(dst))
        return reel(src, dst)

    def move(src, dst):
        if plein["disque"]:
            raise OSError(errno.ENOSPC, "Espace insuffisant sur le disque", str(src), None,
                          str(dst))
        return reel(src, dst)

    monkeypatch.setattr(livraison.os, "rename", rename)
    monkeypatch.setattr(livraison.shutil, "move", move)
    ffmpeg.durees = [3.0]
    _rendre(tmp_path, clip_id)

    ecarte = _temporaire(tmp_path, clip_id)
    ecarte = ecarte.with_name(ecarte.name + livraison.ECARTE)
    clip = _clip(tmp_path, clip_id)
    assert clip["status"] == "error" and str(ecarte) in clip["error"], clip["error"]
    assert "Espace insuffisant" in clip["error"], clip["error"]
    assert "fermez le programme" not in clip["error"], clip["error"]
    assert ecarte.is_file() and _sorties(tmp_path) == [ecarte.name]

    ffmpeg.durees = [3.0]
    ffmpeg.rendus = 0
    _rendre(tmp_path, clip_id)

    clip = _clip(tmp_path, clip_id)
    assert clip["status"] == "error" and "Espace insuffisant" in clip["error"], clip["error"]
    # Le nom d'attente ne s'empile pas : un .ecarte.ecarte echapperait aux
    # candidats des rendus suivants, donc resterait pour toujours.
    assert _sorties(tmp_path) == [ecarte.name], _sorties(tmp_path)

    plein["disque"] = False
    ffmpeg.durees = [None]
    ffmpeg.rendus = 0
    _rendre(tmp_path, clip_id)

    clip = _clip(tmp_path, clip_id)
    assert clip["status"] == "done", clip["error"]
    assert not ecarte.exists()
    assert _sorties(tmp_path) == [Path(clip["output_path"]).name]
    assert _mis_de_cote(tmp_path, clip_id) == ["orphelin.mp4"]


def test_une_retouche_ne_remplace_pas_le_livrable_qu_un_autre_clip_a_pris(tmp_path, ffmpeg):
    """Fix round 3 (F3), scenario du relecteur : A livre X.mp4, B « X (2).mp4 » ;
    l'utilisateur retire X.mp4 de son dossier, la retouche de B descend sur
    X.mp4. La retouche de A faisait alors os.replace sur son output_path perime
    et ecrasait le rendu de B, sous deux lignes 'done' qui pointaient le meme
    fichier."""
    db_path = tmp_path / "clipforge.db"
    a = _seed(db_path)
    b = _clone_de_clip(db_path, a)

    def rendre(clip_id, duree):
        ffmpeg.durees = [duree]
        ffmpeg.rendus = 0
        _rendre(tmp_path, clip_id)
        return Path(_clip(tmp_path, clip_id)["output_path"])

    x = rendre(a, 9.9)
    x2 = rendre(b, 9.95)
    assert x.name.endswith(" - mon clip.mp4") and x2.name.endswith(" - mon clip (2).mp4")

    corbeille = tmp_path / "corbeille"
    corbeille.mkdir()
    x.rename(corbeille / x.name)

    assert rendre(b, 10.0) == x, "la retouche de B descend sur le nom libere"
    pris_par_a = rendre(a, 10.05)

    assert pris_par_a != x, pris_par_a
    assert pris_par_a.name.endswith(" - mon clip (3).mp4"), pris_par_a
    assert x.read_text(encoding="utf-8") == "mp4 10.0", "le rendu de B a ete ecrase"
    assert pris_par_a.read_text(encoding="utf-8") == "mp4 10.05"
    lignes = [_clip(tmp_path, c) for c in (a, b)]
    assert [ligne["status"] for ligne in lignes] == ["done", "done"]
    assert len({ligne["output_path"] for ligne in lignes}) == 2


def test_le_temporaire_d_un_premier_rendu_interrompu_suit_le_changement_de_dossier(
        tmp_path, ffmpeg):
    """Fix round 3 (F3) : un PREMIER rendu interrompu ne laisse aucun livrable,
    donc aucun dossier a deduire de la base ; si le reglage change ensuite, plus
    rien ne ramassait son temporaire. La note du verrou dit ou il est reste."""
    db_path = tmp_path / "clipforge.db"
    clip_id = _seed(db_path)
    ancien = tmp_path / "ancien"
    ancien.mkdir()
    orphelin = livraison.part_path(ancien, clip_id)
    orphelin.write_text("mp4 3.0", encoding="utf-8")
    with verrou_du_clip(tmp_path / "jobs" / f"clip{clip_id}") as verrou:
        verrou.noter(orphelin)
    conn = dbmod.get_db(db_path)
    try:
        dbmod.set_setting(conn, "output_dir", str(tmp_path / "nouveau"))
        conn.commit()
    finally:
        conn.close()

    _rendre(tmp_path, clip_id)

    assert _clip(tmp_path, clip_id)["status"] == "done"
    assert not orphelin.exists()
    assert _mis_de_cote(tmp_path, clip_id) == ["orphelin.mp4"]


def test_une_source_tenue_ouverte_ne_fait_pas_boucler_la_promotion(tmp_path, monkeypatch):
    """Mutant survivant (fix round 3, F4) : `except OSError` a la place de
    FileExistsError faisait tourner la promotion sur les rangs sans fin quand
    c'est le rendu LUI-MEME qui ne peut pas etre renomme. Seul un nom deja pris
    fait passer au rang suivant."""
    vise = tmp_path / "titre.mp4"
    rendu = tmp_path / "rendu.part.mp4"
    rendu.write_bytes(b"mien")
    essais = []

    def renommer(src, dst):
        essais.append(Path(dst).name)
        if len(essais) > 3:
            raise AssertionError(f"la promotion boucle sur les rangs : {essais}")
        raise PermissionError(13, "source tenue ouverte", str(src), 32, str(dst))

    monkeypatch.setattr(livraison, "renommer_sans_ecraser", renommer)
    with pytest.raises(PermissionError):
        livraison.promouvoir(rendu, vise, deja_sien=None)
    assert essais == ["titre.mp4"], essais
    assert rendu.read_bytes() == b"mien"


def _poser_livrable(conn, clip_id: int, chemin: str) -> None:
    conn.execute("UPDATE clips SET output_path=? WHERE id=?", (chemin, clip_id))
    conn.commit()


def test_un_livrable_ecrit_autrement_reste_celui_de_son_clip(tmp_path):
    """Re-revue du fix round 3 : la garde comparait clips.output_path par un `=`
    SQL, quand la promotion compare des chemins. Deux ecritures du meme chemin
    (un point de plus ici, une casse differente sous Windows) rendaient le
    fichier de l'autre clip « libre » ; os.replace, lui, le voyait, et le
    montage de l'autre clip etait detruit."""
    db_path = tmp_path / "clipforge.db"
    a = _seed(db_path)
    b = _clone_de_clip(db_path, a)
    livrable = tmp_path / "output" / "mon clip.mp4"
    conn = dbmod.get_db(db_path)
    try:
        _poser_livrable(conn, a, str(livrable))
        detour = f"{livrable.parent}{os.sep}.{os.sep}{livrable.name}"
        assert naming_mod.livrable_du_clip(conn, b, detour) is None
        assert naming_mod.livrable_du_clip(conn, a, str(livrable)) == livrable
    finally:
        conn.close()


@casse_repliee
def test_la_casse_du_dossier_de_sortie_ne_libere_pas_le_livrable_d_un_autre(tmp_path):
    """Mesure du relecteur : le dossier retape en minuscules suffisait, et le
    rendu de A (13,7 Mo) ecrasait celui de B (10,6 Mo), deux lignes 'done' sur
    un seul fichier."""
    db_path = tmp_path / "clipforge.db"
    a = _seed(db_path)
    b = _clone_de_clip(db_path, a)
    livrable = tmp_path / "output" / "mon clip.mp4"
    conn = dbmod.get_db(db_path)
    try:
        _poser_livrable(conn, a, str(livrable))
        assert naming_mod.livrable_du_clip(conn, b, str(livrable).upper()) is None
    finally:
        conn.close()


def test_un_vrai_rendu_note_son_temporaire_dans_le_verrou(tmp_path, ffmpeg):
    """Le mutant qui retirait verrou.noter() de render_clip_job laissait la suite
    verte : les deux tests de la note l'ecrivaient eux-memes (re-revue du fix
    round 3). C'est ce cablage qui retrouve le temporaire d'un premier rendu
    interrompu quand le dossier de sortie a change depuis."""
    clip_id = _seed(tmp_path / "clipforge.db")
    _rendre(tmp_path, clip_id)

    note = temporaire_note(tmp_path / "jobs" / f"clip{clip_id}")
    assert note == livraison.part_path(tmp_path / "output", clip_id), note
