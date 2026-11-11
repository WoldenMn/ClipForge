"""Bail GPU aupres du service local gpu-queue, optionnel.

Whisper tourne dans CE process, sur la carte fixee par CLIPFORGE_GPU_INDEX :
le service ne peut pas executer la tache a notre place, il ne fait que
reserver la carte (kind=lease) pour qu'une generation Flux ou un LLM ne
s'y superpose pas. Sans service joignable, on transcrit quand meme et on
le DIT dans le journal : un service d'ordonnancement absent n'est pas une
raison de bloquer une analyse.
"""
import json
import logging
import os
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from contextlib import contextmanager

from clipforge import config

logger = logging.getLogger(__name__)

DEFAUT_GPUQ_URL = "http://127.0.0.1:8790"


def _url() -> str:
    """Adresse du service, lue **a chaque appel**.

    Surtout pas une constante de module : elle serait figee a l'import,
    donc une fixture de test qui pose GPUQ_URL apres le premier import du
    module n'aurait aucun effet. Constate le 2026-08-29 : la suite
    attendait un vrai bail malgre la fixture censee la couper.

    Chaine vide = bail desactive.
    """
    return os.environ.get("GPUQ_URL", DEFAUT_GPUQ_URL)
# Pic mesure le 2026-08-16 pour large-v3 float16, 2 workers : 5 391 MiB.
VRAM_GB_PAR_MODELE = {"large-v3": 5.4, "large-v2": 5.4, "medium": 3.0, "small": 1.5,
                      "base": 1.0, "tiny": 0.8}
HttpFn = Callable[[str, str, dict | None], tuple[int, dict]]


def _http(method: str, url: str, body: dict | None) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as exc:
        return exc.code, {}


def _prefer() -> str:
    return "display" if config.WHISPER_GPU_INDEX == 0 else "compute"


@contextmanager
def bail_gpu(model_name: str, *, http: HttpFn = _http, attente_max_s: float = 300,
             intervalle_s: float = 2.0, horloge=time.monotonic, dormir=time.sleep):
    """Reserve la carte Whisper le temps du bloc, puis la rend.

    Le bail est une **optimisation**, jamais une condition de correction :
    il evite qu'une autre charge lourde tombe sur la meme carte. Donc tout
    ce qui peut mal tourner cote service se solde par un avertissement et
    une transcription qui part quand meme :

    - service injoignable ou refus,
    - **attente trop longue** : mesure le 2026-08-29, un bail abandonne par
      un process mort avait fige la carte, et l'ancienne borne d'une heure
      transformait ca en blocage silencieux (un test a tourne 400 s sans
      rien dire avant d'etre tue). Cinq minutes suffisent a laisser passer
      une vraie tache concurrente ; au-dela, mieux vaut transcrire.
    """
    base = _url()
    if not base:
        yield None
        return
    vram = VRAM_GB_PAR_MODELE.get(model_name, 5.4)
    try:
        status, job = http("POST", f"{base}/jobs",
                           {"kind": "lease", "vram_gb": vram, "prefer": _prefer()})
    except (OSError, ValueError) as exc:
        logger.warning("gpu-queue injoignable (%s) : transcription sans bail", exc)
        yield None
        return
    if status != 202 or "id" not in job:
        logger.warning("gpu-queue a refuse le bail (HTTP %s) : transcription sans bail", status)
        yield None
        return
    job_id = job["id"]
    debut = horloge()
    accorde = True
    try:
        while job.get("state") == "queued":
            if horloge() - debut > attente_max_s:
                logger.warning(
                    "bail %s toujours en attente apres %s s : transcription sans bail",
                    job_id, attente_max_s)
                accorde = False
                break
            dormir(intervalle_s)
            _, job = http("GET", f"{base}/jobs/{job_id}", None)
        if accorde:
            logger.info("bail gpu-queue %s accorde sur cuda:%s", job_id, job.get("gpu"))
        yield job_id if accorde else None
    finally:
        try:
            http("DELETE", f"{base}/jobs/{job_id}", None)
        except (OSError, ValueError) as exc:
            logger.error("bail %s non libere (%s) : la carte reste marquee occupee", job_id, exc)
