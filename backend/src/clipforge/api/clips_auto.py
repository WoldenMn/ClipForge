"""Le sort d'un clip auto : garde ou rejete par l'utilisateur.

Separe de `clips.py` (creation/retouche/rendu, deja pres du seuil de taille) :
ces deux routes forment un couple coherent, le geste V5 qui alimente la mesure
d'usage sur les candidats que la machine propose sans qu'on le lui demande.
"""
import sqlite3

from fastapi import APIRouter, Depends, HTTPException

from clipforge import usage
from clipforge.api.deps import get_conn

router = APIRouter(prefix="/api/clips", tags=["clips"])


@router.post("/{clip_id}/keep")
def keep_clip(clip_id: int, conn: sqlite3.Connection = Depends(get_conn)):
    """Marque un clip auto comme garde : le geste qui compte pour la mesure V4.

    409 si le clip n'est pas `origin='auto'` — « garder » n'a de sens que pour
    un candidat que la machine a propose sans qu'on le lui demande ; un clip
    de l'utilisateur est deja son propre choix, rien a mesurer dessus ici.
    """
    row = conn.execute("SELECT * FROM clips WHERE id=?", (clip_id,)).fetchone()
    if not row:
        raise HTTPException(404)
    if row["origin"] != "auto":
        raise HTTPException(409, "Ce clip n'est pas issu de la generation automatique")
    usage.record_auto_kept(conn, vod_id=row["vod_id"], segment_id=row["segment_id"])
    return dict(row)


@router.delete("/{clip_id}")
def delete_clip(clip_id: int, conn: sqlite3.Connection = Depends(get_conn)):
    """Supprime un clip. Un clip auto rejete par l'utilisateur EST une mesure,
    mais elle part APRES la suppression, jamais avant.

    L'ordre inverse (mesure puis suppression) laissait un rejet durable pour
    un clip toujours vivant des que le DELETE echouait ensuite (base
    verrouillee par le process de rendu : scenario realiste, pas theorique) —
    et un double-clic pouvait faire ecrire deux evenements pour une seule
    suppression reelle. `row` est deja un `sqlite3.Row` materialise par le
    SELECT ci-dessus : vod_id/segment_id restent lisibles apres coup, la
    suppression ne les efface pas de la memoire du process. Meme discipline
    que /render (clips.py) : commit d'abord ce qui a reellement eu lieu,
    mesure ensuite. `cursor.rowcount == 0` (ligne deja partie entre le SELECT
    et le DELETE — course entre deux requetes) ferme le meme trou par
    l'autre bout : 404, sans evenement, plutot qu'un rejet pour un clip
    deja disparu.
    """
    row = conn.execute("SELECT * FROM clips WHERE id=?", (clip_id,)).fetchone()
    if not row:
        raise HTTPException(404)
    if row["status"] in ("queued", "rendering"):
        # Le rendu auto tourne DANS le process du pipeline, pas via
        # app.state.rendering : le statut en base est le seul garde qui couvre
        # les deux chemins. Le bouton de l'interface est deja desactive, mais
        # sa fenetre de poll (2 s), un second onglet ou un appel direct
        # passeraient sans ce refus — et supprimer la ligne pendant que
        # render_clip_job ecrit orpheline le fichier de sortie.
        raise HTTPException(409, "Montage en cours — supprime le clip quand il sera termine.")
    cur = conn.execute("DELETE FROM clips WHERE id=?", (clip_id,))
    if cur.rowcount == 0:
        raise HTTPException(404)
    conn.commit()
    if row["origin"] == "auto":
        usage.record_auto_rejected(conn, vod_id=row["vod_id"], segment_id=row["segment_id"])
    return {"deleted": True}
