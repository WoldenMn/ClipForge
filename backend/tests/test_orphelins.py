"""Le rangement des temporaires de rendu au demarrage (render.orphelins, appele
par la purge des caches de jobs).

Revue finale V6, fix round 3 (F3) : un rendu tue laisse
clipforge-c<id>.part.mp4 dans le dossier de livraison ; le clip supprime
ensuite, plus aucune retouche ne venait le ramasser (mesure du relecteur :
10,5 Mo restes pour toujours apres un taskkill puis une suppression du clip).
"""
import os
from pathlib import Path

from clipforge import db as dbmod
from clipforge.render import livraison
from clipforge.render.orphelins import ranger_temporaires
from clipforge.render.verrou import verrou_du_clip


def _base(tmp_path: Path):
    """Une base avec un clip ; rend (connexion, id du clip)."""
    conn = dbmod.get_db(tmp_path / "clipforge.db")
    vod_id = dbmod.create_vod(conn, title="t", source_path="x.mp4")
    clip_id = conn.execute("INSERT INTO clips (vod_id, start_s, end_s) VALUES (?,0.0,10.0)",
                           (vod_id,)).lastrowid
    conn.commit()
    return conn, clip_id


def _sortie(tmp_path: Path, nom: str = "output") -> Path:
    dossier = tmp_path / nom
    dossier.mkdir()
    return dossier


def test_le_temporaire_d_un_clip_vivant_part_dans_son_job(tmp_path, caplog):
    conn, clip_id = _base(tmp_path)
    out = _sortie(tmp_path)
    part = livraison.part_path(out, clip_id)
    part.write_text("mp4 3.0", encoding="utf-8")

    with caplog.at_level("WARNING"):
        ranger_temporaires(conn, tmp_path / "jobs", out)

    assert not part.exists()
    garde = tmp_path / "jobs" / f"clip{clip_id}" / "orphelin.mp4"
    assert garde.read_text(encoding="utf-8") == "mp4 3.0"
    assert "rendu interrompu" in caplog.text and part.name in caplog.text, caplog.text


def test_le_temporaire_d_un_clip_supprime_part_dans_le_poubelle_des_jobs(tmp_path):
    """La purge entretient les dossiers de job des clips vivants : les restes
    d'un clip mort n'ont rien a y faire. (Son id, lui, n'est jamais reattribue :
    clips.id est AUTOINCREMENT, mesure de la re-revue du fix round 3.)"""
    conn, clip_id = _base(tmp_path)
    out = _sortie(tmp_path)
    part = livraison.part_path(out, clip_id + 1)
    part.write_text("mp4 3.0", encoding="utf-8")

    ranger_temporaires(conn, tmp_path / "jobs", out)

    assert not part.exists()
    garde = tmp_path / "jobs" / "_poubelle" / f"clip{clip_id + 1}-orphelin.mp4"
    assert garde.read_text(encoding="utf-8") == "mp4 3.0"
    assert not (tmp_path / "jobs" / f"clip{clip_id + 1}" / "orphelin.mp4").exists()


def test_le_nom_d_attente_est_range_aussi(tmp_path):
    """Un deplacement inter-volumes interrompu laisse le .ecarte : meme sort."""
    conn, clip_id = _base(tmp_path)
    out = _sortie(tmp_path)
    part = livraison.part_path(out, clip_id)
    ecarte = part.with_name(part.name + livraison.ECARTE)
    ecarte.write_text("mp4 3.0", encoding="utf-8")

    ranger_temporaires(conn, tmp_path / "jobs", out)

    assert not ecarte.exists()
    assert (tmp_path / "jobs" / f"clip{clip_id}" / "orphelin.mp4").is_file()


def test_le_temporaire_d_un_ancien_dossier_de_sortie_est_retrouve_par_la_note(tmp_path):
    """Premier rendu interrompu : aucun livrable ne dit ou il ecrivait, et le
    reglage a change depuis. La note du verrou, elle, le sait."""
    conn, clip_id = _base(tmp_path)
    ancien, nouveau = _sortie(tmp_path, "ancien"), _sortie(tmp_path, "nouveau")
    part = livraison.part_path(ancien, clip_id)
    part.write_text("mp4 3.0", encoding="utf-8")
    job = tmp_path / "jobs" / f"clip{clip_id}"
    with verrou_du_clip(job) as verrou:
        verrou.noter(part)

    ranger_temporaires(conn, tmp_path / "jobs", nouveau)

    assert not part.exists() and (job / "orphelin.mp4").is_file()


def test_le_temporaire_d_un_rendu_en_cours_est_laisse_en_place(tmp_path, caplog):
    """Le verrou du clip est tenu : ce temporaire est en train d'etre ecrit."""
    conn, clip_id = _base(tmp_path)
    out = _sortie(tmp_path)
    part = livraison.part_path(out, clip_id)
    part.write_text("mp4 3.0", encoding="utf-8")

    with verrou_du_clip(tmp_path / "jobs" / f"clip{clip_id}"), caplog.at_level("INFO"):
        ranger_temporaires(conn, tmp_path / "jobs", out)

    assert part.is_file()
    assert "un rendu de ce clip est en cours" in caplog.text, caplog.text


def test_ce_qui_n_est_pas_un_de_nos_temporaires_n_est_pas_touche(tmp_path):
    conn, _clip_id = _base(tmp_path)
    out = _sortie(tmp_path)
    etrangers = ["mes vacances.mp4", "2026-09-26 - mon clip.mp4", "clipforge-c1.part.mp4.txt",
                 "clipforge-cX.part.mp4"]
    for nom in etrangers:
        (out / nom).write_text("a moi", encoding="utf-8")

    ranger_temporaires(conn, tmp_path / "jobs", out)

    assert sorted(p.name for p in out.iterdir()) == sorted(etrangers)
    assert not (tmp_path / "jobs").exists()


def test_le_reglage_du_dossier_de_sortie_est_suivi(tmp_path):
    """Le dossier ou l'utilisateur livre n'est pas celui de la configuration."""
    conn, clip_id = _base(tmp_path)
    regle = _sortie(tmp_path, "chez moi")
    dbmod.set_setting(conn, "output_dir", str(regle))
    conn.commit()
    part = livraison.part_path(regle, clip_id)
    part.write_text("mp4 3.0", encoding="utf-8")

    ranger_temporaires(conn, tmp_path / "jobs", _sortie(tmp_path, "defaut"))

    assert not part.exists()
    assert (tmp_path / "jobs" / f"clip{clip_id}" / "orphelin.mp4").is_file()


def _lier(lien: Path, cible: Path) -> None:
    """Un lien qui existe partout : jonction sous Windows (aucun privilege
    requis), lien symbolique ailleurs -- donc en CI."""
    if os.name == "nt":
        import _winapi
        _winapi.CreateJunction(str(cible), str(lien))
    else:
        lien.symlink_to(cible, target_is_directory=True)


def test_un_lien_dans_jobs_n_est_pas_suivi_pour_ecrire(tmp_path, caplog):
    """Re-revue du fix round 3 : _dossiers_de_sortie honorait la regle pour LIRE
    la note, _ranger ne l'honorait pas pour ECRIRE. Mesure du relecteur : une
    jonction jobs/clip1 recevait .verrou et orphelin.mp4 dans sa cible, hors de
    jobs/, ou la purge ne peut plus rien (elle refuse les dossiers de job lies)."""
    conn, clip_id = _base(tmp_path)
    out = _sortie(tmp_path)
    part = livraison.part_path(out, clip_id)
    part.write_text("mp4 3.0", encoding="utf-8")
    jobs, dehors = tmp_path / "jobs", tmp_path / "ailleurs"
    jobs.mkdir()
    dehors.mkdir()
    _lier(jobs / f"clip{clip_id}", dehors)

    with caplog.at_level("WARNING"):
        ranger_temporaires(conn, jobs, out)

    assert part.read_text(encoding="utf-8") == "mp4 3.0", "le temporaire reste en place"
    assert list(dehors.iterdir()) == []
    assert "est un lien" in caplog.text, caplog.text


def test_un_poubelle_lie_n_est_pas_suivi(tmp_path, caplog):
    """Meme garde pour le dossier des restes : un _poubelle lie menerait les
    temporaires des clips supprimes hors de jobs/."""
    conn, clip_id = _base(tmp_path)
    out = _sortie(tmp_path)
    part = livraison.part_path(out, clip_id + 1)
    part.write_text("mp4 3.0", encoding="utf-8")
    jobs, dehors = tmp_path / "jobs", tmp_path / "ailleurs"
    jobs.mkdir()
    dehors.mkdir()
    _lier(jobs / "_poubelle", dehors)

    with caplog.at_level("WARNING"):
        ranger_temporaires(conn, jobs, out)

    assert part.read_text(encoding="utf-8") == "mp4 3.0"
    assert list(dehors.iterdir()) == []
    assert "est un lien" in caplog.text, caplog.text


def test_un_dossier_illisible_ne_bloque_pas_le_demarrage(tmp_path, caplog, monkeypatch):
    """Re-revue du fix round 3 : une PermissionError sur le dossier de livraison
    (droits, partage reseau, dossier cloud en mauvais etat) remontait par la
    purge jusqu'au lifespan -- l'application ne demarrait plus, sur l'echec
    d'une etape de menage."""
    conn, _clip_id = _base(tmp_path)
    out = _sortie(tmp_path)
    reel = Path.iterdir

    def iterdir(self):
        if self == out:
            raise PermissionError(13, "acces refuse")
        return reel(self)

    monkeypatch.setattr(Path, "iterdir", iterdir)
    with caplog.at_level("WARNING"):
        ranger_temporaires(conn, tmp_path / "jobs", out)

    assert "non inspecte" in caplog.text and out.name in caplog.text, caplog.text


def test_un_temporaire_a_l_id_non_canonique_n_est_pas_range(tmp_path):
    """clipforge-c007 n'a jamais ete ecrit par ClipForge : le ranger dans le job
    du clip 7 le ferait passer pour un de ses artefacts. Meme garde que la purge
    des caches (fix round 2, K5) sur les noms rembourres et les chiffres
    Unicode."""
    conn, clip_id = _base(tmp_path)
    out = _sortie(tmp_path)
    noms = [f"clipforge-c00{clip_id}.part.mp4", "clipforge-c\u0663.part.mp4"]
    for nom in noms:
        (out / nom).write_text("pas a nous", encoding="utf-8")

    ranger_temporaires(conn, tmp_path / "jobs", out)

    assert sorted(p.name for p in out.iterdir()) == sorted(noms)
    assert not (tmp_path / "jobs").exists()
