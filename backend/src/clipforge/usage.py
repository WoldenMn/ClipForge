"""Journal d'usage local : ce que l'utilisateur fait des candidats proposes.

Tout reste dans la base SQLite de la machine. Rien ne part ailleurs sans une
action explicite de l'utilisateur (rapport de diagnostic). Ce n'est pas de la
telemetrie.
"""
import logging
import sqlite3

logger = logging.getLogger(__name__)

KINDS = ("clip_created", "clip_exported", "bounds_adjusted",
        "auto_clip_kept", "auto_clip_rejected")


def record(conn: sqlite3.Connection, kind: str, *, vod_id: int | None = None,
           segment_id: int | None = None, rank: int | None = None,
           top_signal: str | None = None, start_shift_s: float | None = None,
           end_shift_s: float | None = None) -> None:
    """Consigne un evenement d'usage.

    Ne leve JAMAIS. C'est l'exception documentee a la regle « jamais avaler une
    erreur » (spec V4) : une observation ratee ne doit pas faire tomber l'action
    qu'elle observe. L'echec part dans le journal, la ou il sera vu au
    diagnostic, et nulle part ailleurs.
    """
    try:
        conn.execute(
            "INSERT INTO usage_events (kind, vod_id, segment_id, rank, top_signal, "
            "start_shift_s, end_shift_s) VALUES (?,?,?,?,?,?,?)",
            (kind, vod_id, segment_id, rank, top_signal, start_shift_s, end_shift_s))
        conn.commit()
    except sqlite3.Error as exc:
        logger.warning("evenement d'usage %s non consigne : %s", kind, exc)


def _candidat(conn: sqlite3.Connection, segment_id: int | None,
              colonnes: str) -> sqlite3.Row | None:
    """Lit le candidat, ou rend None en disant pourquoi.

    Cette lecture appartient a l'observation, pas a l'action observee : elle
    porte donc la MEME garde que l'ecriture. Laissee chez l'appelant, un SELECT
    tombe sur le verrou du process d'analyse (get_db_light expire a 5 s) rendait
    500 sur /render alors que status='queued' etait deja commite et que
    run_in_executor n'avait jamais ete atteint : le clip restait bloque jusqu'au
    prochain redemarrage. Une observation ratee coutait l'export qu'elle observe.
    """
    if not segment_id:
        return None
    try:
        return conn.execute(f"SELECT {colonnes} FROM segments WHERE id=?",
                            (segment_id,)).fetchone()
    except sqlite3.Error as exc:
        logger.warning("candidat %s illisible, evenement sans contexte : %s",
                       segment_id, exc)
        return None


def record_export(conn: sqlite3.Connection, *, vod_id: int | None,
                  segment_id: int | None) -> None:
    """Consigne un export, rang et signal dominant du candidat lus ici meme."""
    seg = _candidat(conn, segment_id, "rank, top_signal")
    record(conn, "clip_exported", vod_id=vod_id, segment_id=segment_id,
           rank=seg["rank"] if seg else None,
           top_signal=seg["top_signal"] if seg else None)


def _record_ignore_duplicates(conn: sqlite3.Connection, kind: str, *, vod_id: int | None,
                              segment_id: int | None, rank: int | None,
                              top_signal: str | None) -> None:
    """Variante atomique de `record()` : INSERT OR IGNORE au lieu d'INSERT.

    Reservee aux `kind` proteges par un index unique partiel (`ux_usage_kept`,
    db.py) : l'unicite est alors tranchee par SQLite lui-meme au moment de
    l'ecriture, pas par un SELECT prealable cote application qui laisserait
    une fenetre entre lecture et ecriture ouverte a une course entre deux
    connexions concurrentes (deux clics rapides sur le threadpool FastAPI).
    Meme contrat que `record()` : ne leve jamais.
    """
    try:
        conn.execute(
            "INSERT OR IGNORE INTO usage_events (kind, vod_id, segment_id, rank, top_signal) "
            "VALUES (?,?,?,?,?)",
            (kind, vod_id, segment_id, rank, top_signal))
        conn.commit()
    except sqlite3.Error as exc:
        logger.warning("evenement d'usage %s non consigne : %s", kind, exc)


def record_auto_kept(conn: sqlite3.Connection, *, vod_id: int | None,
                     segment_id: int | None) -> None:
    """Consigne la conservation d'un clip auto — un geste, jamais deux.

    Unicite tenue par l'index partiel `ux_usage_kept` (db.py) sur segment_id,
    pour les `kind='auto_clip_kept'` a segment_id non NULL : l'INSERT OR
    IGNORE est atomique, donc deux requetes concurrentes ne peuvent plus
    toutes les deux inserer. usage_events n'a pas de colonne clip_id et ne
    doit pas en gagner une pour ce seul besoin (spec V5 : la table mesure
    l'ALGORITHME, elle ne doit pas se mettre a identifier des clips). Un clip
    auto qui a survecu a une re-analyse peut avoir `segment_id` a NULL
    (cascade ON DELETE SET NULL sur segments) : l'index ne couvre pas ce cas
    (WHERE segment_id IS NOT NULL), et l'idempotence y reste assumee comme
    best-effort plutot que garantie.
    """
    seg = _candidat(conn, segment_id, "rank, top_signal")
    _record_ignore_duplicates(conn, "auto_clip_kept", vod_id=vod_id, segment_id=segment_id,
                              rank=seg["rank"] if seg else None,
                              top_signal=seg["top_signal"] if seg else None)


def record_auto_rejected(conn: sqlite3.Connection, *, vod_id: int | None,
                         segment_id: int | None) -> None:
    """Consigne le rejet d'un clip auto, rang et signal du candidat lus ici meme.

    Appele APRES la suppression du clip, sur des vod_id/segment_id deja
    captures par l'appelant avant le DELETE (le SELECT initial de l'endpoint) :
    on ne mesure que ce qui a reellement eu lieu, jamais une intention qui
    pourrait encore echouer derriere (cf. api/clips_auto.py:delete_clip).
    segment_id reste valide a lire ici meme si le clip a disparu : c'est la
    table `segments`, pas `clips`, qui est interrogee par _candidat().
    """
    seg = _candidat(conn, segment_id, "rank, top_signal")
    record(conn, "auto_clip_rejected", vod_id=vod_id, segment_id=segment_id,
           rank=seg["rank"] if seg else None, top_signal=seg["top_signal"] if seg else None)


def record_bound_shift(conn: sqlite3.Connection, *, vod_id: int | None,
                       segment_id: int | None, start_s: float, end_s: float) -> None:
    """Consigne l'ecart des bornes retenues face au CANDIDAT D'ORIGINE.

    Jamais face a la valeur precedente du clip : ce qu'on cherche a savoir est
    de combien le decoupage automatique se trompe, pas combien de fois
    l'utilisateur a tatonne. Deux retouches successives donnent donc deux ecarts
    lus depuis le meme candidat.

    Sans candidat lisible il n'y a pas d'ecart a mesurer : rien n'est ecrit,
    plutot qu'une ligne dont les deux colonnes mentiraient.
    """
    seg = _candidat(conn, segment_id, "start_s, end_s")
    if not seg:
        return
    record(conn, "bounds_adjusted", vod_id=vod_id, segment_id=segment_id,
           start_shift_s=start_s - seg["start_s"], end_shift_s=end_s - seg["end_s"])
