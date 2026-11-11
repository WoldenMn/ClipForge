"""Les trois etapes du mode automatique : fabrication, ecriture, montage en serie.

Aucune n'ecrit d'evenement d'usage. La regle V4 dit qu'un evenement compte un
GESTE de l'utilisateur ; une machine qui fabrique cinq clips toute seule n'en
fait aucun. Ce que l'utilisateur en fait ensuite (garder, rejeter, retoucher)
est consigne par l'API, sur clic.

Le contrat de reprise est le meme partout : chaque etape saute ce qui est deja
fait plutot que de le refaire, et ne SUPPRIME jamais un clip que l'utilisateur
voit dans sa grille.
"""
import logging
import sqlite3

from clipforge import config
from clipforge import db as dbmod
from clipforge.pipeline.ai_writing import write_clips
from clipforge.pipeline.runner import build_progress_cb
from clipforge.render.queue import render_clip_job

logger = logging.getLogger(__name__)

AUTO_CLIP_COUNT_DEFAUT = int(dbmod.DEFAULT_SETTINGS["auto_clip_count"])


def auto_clip_count(conn: sqlite3.Connection) -> int:
    """Nombre de clips a fabriquer par VOD ; 0 coupe le mode automatique.

    Une valeur illisible ne coupe pas le mode auto en silence : elle est
    signalee et le defaut reprend la main. Retomber sur 0 rendrait un reglage
    corrompu indiscernable d'un choix de l'utilisateur — la VOD finirait 'ready'
    sans un seul clip, et sans une ligne pour le dire.
    """
    brut = dbmod.get_setting(conn, "auto_clip_count")
    try:
        return max(0, int(brut))
    except (TypeError, ValueError):
        logger.warning("Reglage auto_clip_count illisible (%r) : defaut %s applique",
                       brut, AUTO_CLIP_COUNT_DEFAUT)
        return AUTO_CLIP_COUNT_DEFAUT


def _db_path(conn: sqlite3.Connection) -> str:
    """Le fichier que CETTE connexion ecrit, pas celui que la config designe.

    render_clip_job tourne d'ordinaire dans son propre process et recoit le
    chemin de l'API. Appele ici, il doit rouvrir la MEME base que l'etape :
    partir de config.DB_PATH monterait les clips d'une autre base des que les
    deux divergent (suite de tests, CLIPFORGE_DATA deplace entre deux
    lancements). La connexion, elle, ne peut pas se tromper sur son propre
    fichier.
    """
    ligne = conn.execute("PRAGMA database_list").fetchone()
    return (ligne["file"] if ligne else "") or str(config.DB_PATH)


def step_autoclips(conn: sqlite3.Connection, vod_id: int, ctx: dict) -> None:
    """Les N meilleurs candidats deviennent des clips 'auto', profil et style poses.

    Un candidat qui porte deja un clip auto est saute : une reprise apres coupure
    ne doit pas doubler la grille. Rien n'est supprime en revanche. Apres une
    RE-analyse, les candidats sont remplaces et les clips de la passe precedente
    perdent leur segment_id : ils restent visibles, a cote des nouveaux. C'est
    voulu — la machine ne fait pas disparaitre ce que l'utilisateur regarde.

    Style de sous-titres et profil de scene sont figes ici, comme le fait
    create_clip pour un clip a la main : changer le prereglage plus tard ne
    reecrit pas sous le nez de l'utilisateur des clips deja produits.
    """
    voulus = auto_clip_count(conn)
    if voulus <= 0:
        return
    style = dbmod.get_setting(conn, "subtitle_style_default") or None
    profile_id = dbmod.clip_profile_id(conn, vod_id)
    deja_servis = {r["segment_id"] for r in conn.execute(
        "SELECT segment_id FROM clips WHERE vod_id=? AND origin='auto' "
        "AND segment_id IS NOT NULL", (vod_id,))}
    candidats = conn.execute(
        "SELECT * FROM segments WHERE vod_id=? ORDER BY rank LIMIT ?",
        (vod_id, voulus)).fetchall()
    crees = 0
    for seg in candidats:
        if seg["id"] in deja_servis:
            continue
        conn.execute(
            "INSERT INTO clips (segment_id, vod_id, start_s, end_s, title, subtitle_style, "
            "profile_id, origin) VALUES (?,?,?,?,?,?,?,'auto')",
            (seg["id"], vod_id, seg["start_s"], seg["end_s"], seg["ai_title"] or "",
             style, profile_id))
        crees += 1
    conn.commit()
    logger.info("Vod %s : %s clip(s) automatique(s) cree(s) sur %s candidat(s) retenus",
                vod_id, crees, len(candidats))


def step_writing(conn: sqlite3.Connection, vod_id: int, ctx: dict) -> None:
    """Accroche, titre et emoji des clips auto qui n'en ont pas encore.

    write_clips ecrit ce qu'on lui donne, en ecrasant. Le filtre sur hook IS NULL
    est donc ce qui rend la reprise gratuite (un seul appel d'API par VOD, pas un
    par tentative) ET ce qui protege une accroche deja retouchee dans l'editeur.
    Aucun clip a ecrire n'est pas une erreur : l'etape n'a simplement rien a faire.
    """
    a_ecrire = [r["id"] for r in conn.execute(
        "SELECT id FROM clips WHERE vod_id=? AND origin='auto' AND hook IS NULL ORDER BY id",
        (vod_id,))]
    if not a_ecrire:
        return
    if not write_clips(conn, vod_id, a_ecrire):
        logger.warning("Vod %s : accroches ecrites en repli local (%s clip(s))",
                       vod_id, len(a_ecrire))


def step_autorender(conn: sqlite3.Connection, vod_id: int, ctx: dict) -> None:
    """Monte les clips auto un par un. Un echec est trace et n'arrete pas les autres.

    Les clips deja 'done' sont sautes : remonter quatre clips faits couterait
    plusieurs minutes a chaque reprise, alors que le redemarrage n'a laisse en
    plan que celui qui tournait — bascule en erreur par la reconciliation, donc
    repris ici. Un clip 'queued' ou 'rendering' l'est aussi : le pool de l'API
    le rend deja (« Tout relancer »), et le rendre ici le livrait deux fois
    (revue finale V6, fix round 3, F1).
    """
    a_monter = [r["id"] for r in conn.execute(
        "SELECT id FROM clips WHERE vod_id=? AND origin='auto' "
        "AND status NOT IN ('done', 'queued', 'rendering') ORDER BY id", (vod_id,))]
    if not a_monter:
        return
    db_path = _db_path(conn)
    progression = build_progress_cb(conn, vod_id, "rendering_clips")
    # Demarre le chrono AVANT le premier clip : sans cet appel, l'horloge de
    # progression() ne demarre qu'a son premier appel REEL, qui n'arrive
    # qu'apres le premier montage. La duree du clip 1 n'entre alors jamais
    # dans l'extrapolation et l'ETA affichee est deux fois trop optimiste.
    # MIN_PCT_FOR_ETA (0.0 < 2.0) protege ce premier appel de toute division.
    progression(0.0)
    for fait, clip_id in enumerate(a_monter, start=1):
        echec: Exception | None = None
        try:
            render_clip_job(clip_id, db_path, str(config.JOBS_DIR), str(config.OUTPUT_DIR))
        except Exception as exc:  # noqa: BLE001
            echec = exc
        # render_clip_job rend le journal en sortant (teardown_logging), ce qui
        # est juste pour un process de rendu qui meurt derriere. Appele ici, dans
        # le process d'analyse qui continue, il laisserait la suite du pipeline
        # sans handler : les erreurs de montage suivantes n'atteindraient plus
        # moteur.log, et un echec deviendrait muet la ou il compte le plus.
        # AVANT la ligne d'erreur, jamais apres : c'est l'ordre qui compte, et
        # test_the_second_render_failure_still_reaches_the_engine_journal le tient.
        config.setup_logging()
        if echec is not None:
            # Ce qui remonte JUSQU'ICI a echappe au filet de render_clip_job, qui
            # rattrape tout ce qu'il sait ecrire lui-meme dans clips.error : sans
            # cette ligne, le clip resterait sans trace de son echec.
            logger.error("Montage du clip %s (vod %s) echoue : %s", clip_id, vod_id, echec)
            conn.execute("UPDATE clips SET status='error', error=? WHERE id=?",
                         (str(echec), clip_id))
            conn.commit()
        progression(100.0 * fait / len(a_monter))
