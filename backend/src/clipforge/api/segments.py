import math
import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Query

from clipforge import db as dbmod
from clipforge.api.deps import get_conn

router = APIRouter(prefix="/api/vods", tags=["segments"])

MAX_SIGNAL_POINTS = 600


@router.get("/{vod_id}/segments")
def list_segments(vod_id: int, conn: sqlite3.Connection = Depends(get_conn)):
    rows = conn.execute(
        "SELECT * FROM segments WHERE vod_id=? "
        "ORDER BY COALESCE(ai_score, score * 100) DESC", (vod_id,)).fetchall()
    return [dict(r) for r in rows]


@router.get("/{vod_id}/signals")
def list_signals(vod_id: int, kind: str | None = None,
                 step: float | None = Query(None, gt=0),
                 conn: sqlite3.Connection = Depends(get_conn)):
    """Signaux echantillonnes pour la timeline : moyenne par tranche reguliere.

    Une VOD de 2 h 40 porte ~3 800 mesures ; la timeline en affiche 1 100 pixels.
    On renvoie donc au plus MAX_SIGNAL_POINTS tranches, et comme elles sont
    regulieres, seul le pas voyage : une liste de nombres au lieu d'une liste
    d'objets {t, score}. Une tranche sans mesure vaut null (trou assume, pas zero
    invente). `step` force le pas, sans jamais depasser le plafond de points.
    """
    row = dbmod.get_vod(conn, vod_id)
    if not row:
        raise HTTPException(404)
    span = row["duration_s"] or conn.execute(
        "SELECT MAX(t_s) AS fin FROM signals WHERE vod_id=?", (vod_id,)).fetchone()["fin"]
    if not span or span <= 0:
        return {"duration_s": 0.0, "step_s": 0.0, "series": []}

    step_s = step or span / MAX_SIGNAL_POINTS
    count = max(1, math.ceil(span / step_s))
    if count > MAX_SIGNAL_POINTS:
        count = MAX_SIGNAL_POINTS
        step_s = span / count

    filtre = " AND kind=?" if kind else ""
    params = [step_s, vod_id, kind] if kind else [step_s, vod_id]
    rows = conn.execute(
        "SELECT kind, CAST(t_s / ? AS INTEGER) AS tranche, AVG(score) AS score "
        f"FROM signals WHERE vod_id=?{filtre} GROUP BY kind, tranche", params).fetchall()

    series: dict[str, list[float | None]] = {}
    for r in rows:
        scores = series.setdefault(r["kind"], [None] * count)
        scores[min(int(r["tranche"]), count - 1)] = round(r["score"], 4)
    return {"duration_s": span, "step_s": step_s,
            "series": [{"kind": k, "scores": v} for k, v in sorted(series.items())]}
