"""Ce qui empeche une analyse de demarrer : une autre analyse, ou un montage.

A part de api/vods.py, deja au-dela de 200 lignes. Relancer l'analyse d'une
VOD dont un clip est en file ou en rendu (« Tout relancer ») faisait rendre le
meme clip au pool ET a l'etape de montage du pipeline : livre deux fois,
parfois corrompu et marque 'done' (revue finale V6, fix round 3, F1).
"""
import sqlite3


def refus_d_analyse(conn: sqlite3.Connection, running: dict, vod_id: int) -> str | None:
    """Pourquoi /analyze ne peut pas demarrer maintenant, pour l'utilisateur, ou None."""
    fut = running.get(vod_id)
    if fut and not fut.done():
        return "Analyse deja en cours"
    en_cours = conn.execute(
        "SELECT COUNT(*) FROM clips WHERE vod_id=? AND status IN ('queued', 'rendering')",
        (vod_id,)).fetchone()[0]
    if en_cours:
        return (f"{en_cours} clip(s) de cette VOD en cours de montage : relance l'analyse "
                "quand ils auront fini")
    return None
