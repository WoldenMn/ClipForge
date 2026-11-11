import sqlite3

from fastapi import APIRouter, Depends

from clipforge import health
from clipforge.api.deps import get_conn

# Surtout pas /api/health : ce chemin est la sonde de disponibilite que Tauri
# interroge, et dont le corps {"ok":true} est verifie a l'octet pres cote Rust
# (src-tauri/src/engine_owner.rs, is_engine_response). Le repondre autrement
# ferait croire a l'app qu'un programme tiers detient son port.
router = APIRouter(prefix="/api/health-checks", tags=["health"])


@router.get("")
def get_checks(conn: sqlite3.Connection = Depends(get_conn)):
    """Controles de sante, demandes en tache de fond par l'interface.

    Fonction synchrone volontairement : FastAPI l'execute alors dans son pool de
    threads, ou les sondes systeme (nvidia-smi, ffmpeg -version) et les lectures
    de conteneur peuvent durer sans bloquer la boucle d'evenements.
    """
    return {"controles": health.run_checks_without_db() + health.run_db_checks(conn)}
