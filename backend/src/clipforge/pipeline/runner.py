import logging
import time
import traceback
from collections.abc import Callable
from pathlib import Path

from clipforge import config
from clipforge import db as dbmod
from clipforge.errors import user_message
from clipforge.pipeline.remux import remux_prevu

logger = logging.getLogger(__name__)

STEP_ORDER = ("downloading", "remuxing", "probing", "extracting_audio", "transcribing",
              "analyzing", "segmenting", "ai_scoring",
              "making_clips", "writing_hooks", "rendering_clips")

# Ces libelles sont ceux de STATUS_FR (frontend/src/api/client.ts), a la lettre :
# la carte affiche le badge du frontend juste au-dessus du libelle d'etape venu
# d'ici, et deux orthographes du meme mot cohabitaient dans le meme cadre.
STEP_LABELS = {
    "downloading": "Téléchargement",
    "remuxing": "Indexation vidéo",
    "probing": "Lecture métadonnées",
    "extracting_audio": "Extraction audio",
    "transcribing": "Transcription",
    "analyzing": "Analyse audio et chat",
    "segmenting": "Découpage",
    "ai_scoring": "Scoring IA",
    "making_clips": "Fabrication des clips",
    "writing_hooks": "Écriture des accroches",
    "rendering_clips": "Montage final",
}

MIN_PCT_FOR_ETA = 2.0

StepFn = Callable[[object, int, dict], None]
# Rend la RAISON du refus, deja redigee pour l'utilisateur, ou None si rien
# n'empeche l'etape de demarrer.
PreconditionFn = Callable[[object, int], str | None]


class PreconditionError(Exception):
    """Une etape a demande a ne pas demarrer.

    Ce n'est pas un plantage : le message est deja redige pour l'utilisateur et
    part dans `error` tel quel, sans pile d'appels ni passage par user_message.
    """


def _etape_annoncee(vod) -> str | None:
    """L'etape que la LIGNE dit avoir atteinte. Ne juge rien de l'etat du disque.

    Une VOD prete ou jamais analysee ne renvoie rien : relancer une analyse
    terminee (nouveaux poids, scoring IA active depuis) doit tout rejouer.
    """
    if vod["status"] in STEP_ORDER:
        return vod["status"]  # crash brut : le statut est reste sur l'etape en cours
    if vod["status"] == "error" and vod["error_detail"]:
        # error porte une phrase pour l'utilisateur : l'etape technique vit dans error_detail
        failed = vod["error_detail"].split(":", 1)[0]
        if failed in STEP_ORDER:
            return failed
    return None


def _borner_a_la_source(vod, point: str | None) -> str | None:
    """Ramene la reprise a l'etape que l'etat REEL de la source autorise.

    Deux bornes, dans cet ordre : pas de source du tout, puis une source encore
    a reparer.

    L'etape annoncee dit ou la ligne EST TOMBEE, jamais si l'etat du disque
    autorise a repartir de la. Mesure du 2026-08-30 : la VOD du user portait
    `error_detail = "probing: ..."` au-dessus d'un MPEG-TS brut ; la reprise
    sautait donc telechargement ET remux — c'est-a-dire toute la reparation
    ajoutee par de8258f — pour relire le meme conteneur et remourir en une
    seconde, indefiniment, pendant que le message promettait « Relance
    l'analyse : ClipForge le reparera ».

    L'invariant est donc pose ICI, sur le calcul du point de reprise, et pas
    dans les etapes : c'est le seul endroit ou l'on decide de SAUTER quelque
    chose, donc le seul ou l'on peut refuser de sauter. Les preconditions par
    etape ne pouvaient pas rattraper le cas — celle de `probing` verifie que le
    fichier existe, et le TS existait.

    Quand la source est PRESENTE mais malade, la borne est `remuxing` et jamais
    `downloading` : step_download rend la main sans rien faire tant que
    source_path est renseigne, donc y revenir ne retelechargerait rien et
    couterait un aller-retour pour le meme resultat.

    Quand elle est ABSENTE, tout depend de ce qui peut la ramener. Avec un
    twitch_url, le telechargement le peut : c'est la seule etape qui repare cet
    etat-la, et c'est le second temps de la mise de cote d'une source corrompue
    (D8) — source_path vide sous une ligne qui dit `remuxing:`, qui repartirait
    sinon d'un remux sans rien a remuxer. Sans twitch_url, personne ne le peut :
    revenir en arriere ne ferait que couper la reprise legitime d'une
    transcription, qui ne vit que du wav deja extrait, et les etapes qui lisent
    le fichier le reclament deja par leur precondition.

    Une source saine ne bouge pas d'un pouce : c'est la garantie « on ne
    retranscrit pas quatre heures » qui en depend.
    """
    if point is None:
        return point
    source = Path(vod["source_path"] or "")
    if vod["twitch_url"] and not source.is_file():
        if point != "downloading":
            logger.info("Reprise de la vod %s ramenee de %s a downloading : source absente",
                        vod["id"], point)
        return "downloading"
    if STEP_ORDER.index(point) <= STEP_ORDER.index("remuxing"):
        return point
    defaut = remux_prevu(source, config.DOWNLOADS_DIR)
    if defaut is None:
        return point
    logger.info("Reprise de la vod %s ramenee de %s a remuxing : conteneur %s",
                vod["id"], point, defaut)
    return "remuxing"


def resume_step(vod) -> str | None:
    """Etape a rejouer pour reprendre une analyse interrompue, None pour tout rejouer.

    A appeler AVANT de reecrire le statut de la vod : POST /analyze repasse en
    'queued' et efface error_detail, donc les deux traces lues ici. Le process
    enfant, lui, ne demarre qu'apres — il ne peut plus rien en deduire, d'ou le
    passage du point de reprise en parametre plutot qu'une relecture de la base.

    Deux temps, et l'ordre compte : ce que la ligne annonce, puis ce que la
    source autorise reellement. Site unique du calcul — l'API n'appelle que
    cette fonction, run_steps ne fait que consommer son resultat.
    """
    return _borner_a_la_source(vod, _etape_annoncee(vod))


def _exiger(preconditions: dict[str, PreconditionFn] | None, name: str,
            conn, vod_id: int) -> None:
    """Leve si l'etape a une raison de ne pas demarrer.

    Le plantage d'une precondition n'est PAS rattrape ici : il remonte au
    traitement d'erreur de l'etape, qui garde la trace et prefixe le detail
    technique du nom de l'etape. Un garde-fou casse ne peut donc ni passer
    inapercu, ni couter le point de reprise.
    """
    controle = (preconditions or {}).get(name)
    raison = controle(conn, vod_id) if controle else None
    if raison:
        raise PreconditionError(raison)


def run_steps(conn, vod_id: int, steps: list[tuple[str, StepFn]], ctx: dict,
              resume_from: str | None = None,
              preconditions: dict[str, PreconditionFn] | None = None) -> None:
    """Execute les etapes dans l'ordre, en sautant celles qui precedent resume_from.

    resume_from vient de resume_step(), appelee par l'API avant qu'elle ne
    reinitialise le statut. Sans lui, tout est rejoue : les garde-fous par etape
    (source_path rempli, wav present, words_complete) evitent alors le gros du
    travail, mais pas le rechargement audio ni le re-decoupage.

    preconditions dit, par nom d'etape, ce qu'elle exige pour demarrer. Seules
    les etapes REELLEMENT jouees sont interrogees : ce que la reprise saute est
    deja fait, l'exiger encore bloquerait sur un fichier de travail efface
    depuis. Une etape absente du dictionnaire n'exige rien.
    """
    names = [n for n, _ in steps]
    done_until = names.index(resume_from) - 1 if resume_from in names else -1
    for i, (name, fn) in enumerate(steps):
        if i <= done_until:
            continue
        dbmod.set_vod_status(conn, vod_id, name, progress=0.0,
                             step_label=STEP_LABELS.get(name, name))
        try:
            _exiger(preconditions, name, conn, vod_id)
            fn(conn, vod_id, ctx)
        except PreconditionError as exc:
            logger.error("Etape %s refusee pour vod %s : %s", name, vod_id, exc)
            dbmod.set_vod_error(
                conn, vod_id,
                f"{STEP_LABELS.get(name, name)} : {exc}",
                f"{name}: precondition non satisfaite — {exc}")
            return
        except Exception as exc:  # noqa: BLE001
            logger.error("Etape %s echouee pour vod %s : %s", name, vod_id, exc)
            dbmod.set_vod_error(
                conn, vod_id,
                f"{STEP_LABELS.get(name, name)} : {user_message(exc)}",
                f"{name}: {exc}\n{traceback.format_exc()[-2000:]}")
            return
    dbmod.set_vod_status(conn, vod_id, "ready", progress=100.0)


def build_progress_cb(conn, vod_id: int, status: str, *,
                      clock: Callable[[], float] = time.monotonic):
    """Callback de progression : pourcentage, libelle d'etape et temps restant.

    L'ETA extrapole la vitesse moyenne observee depuis le premier appel de cette
    etape (une seule etape a la fois, donc pas de moyenne glissante). Deux planchers
    avant d'oser une estimation : une seconde ecoulee, et MIN_PCT_FOR_ETA parcourus.
    Le second compte autant que le premier — un telechargement qui annonce 0,3 %
    apres deux secondes extrapole a plus d'une heure pour trois minutes reelles.
    Un pourcentage qui stagne recalcule quand meme l'ETA plutot que de l'effacer :
    l'interface afficherait sinon un temps restant clignotant.

    clock est injectable pour que les tests soient deterministes sans dormir.
    """
    started: dict[str, float | None] = {"t": None}
    label = STEP_LABELS.get(status, status)

    def cb(pct: float) -> None:
        now = clock()
        if started["t"] is None:
            started["t"] = now
        elapsed = now - started["t"]
        assez_avance = elapsed > 1.0 and pct >= MIN_PCT_FOR_ETA
        eta = elapsed * (100.0 - pct) / pct if assez_avance else None
        conn.execute(
            f"UPDATE vods SET progress=?, step_label=?, eta_s=?, {dbmod.TOUCH_UPDATED_AT} "
            f"WHERE id=? AND status=?",
            (pct, label, eta, vod_id, status))
        conn.commit()
    return cb


def run_analysis_pipeline(vod_id: int, db_path: str, resume_from: str | None = None) -> None:
    """Point d'entree du PROCESS ENFANT (spawn Windows : top-level, picklable).

    Ouvre sa propre connexion SQLite (WAL) ; les imports lourds restent dans les steps.
    Les imports de steps et de preconditions sont tardifs : ce module leur fournit
    build_progress_cb et PreconditionFn, les remonter en tete fermerait le cycle.
    """
    from clipforge.pipeline.preconditions import build_preconditions
    from clipforge.pipeline.steps import build_real_steps
    config.setup_logging()
    conn = dbmod.get_db(Path(db_path))
    try:
        run_steps(conn, vod_id, build_real_steps(), ctx={}, resume_from=resume_from,
                  preconditions=build_preconditions())
    finally:
        conn.close()
        config.teardown_logging()
