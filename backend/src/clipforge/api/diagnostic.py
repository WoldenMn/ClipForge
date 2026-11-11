import logging
import sqlite3

from fastapi import APIRouter, Depends, HTTPException

from clipforge import config, diagnostic
from clipforge.api.deps import get_conn

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/diagnostic", tags=["diagnostic"])


@router.get("")
def get_report(conn: sqlite3.Connection = Depends(get_conn)):
    """Rapport transmissible. Ecrit aussi sur disque pour que l'utilisateur
    puisse le joindre a un message sans copier-coller."""
    contenu = diagnostic.build_report(conn)
    chemin = config.DATA_DIR / "diagnostic.txt"
    try:
        chemin.write_text(contenu, encoding="utf-8")
    except OSError as exc:
        logger.exception("rapport de diagnostic non ecrit dans %s", chemin)
        raise HTTPException(
            500, f"Rapport impossible a ecrire dans {chemin} : {exc}") from exc
    return {"contenu": contenu, "chemin": str(chemin)}
