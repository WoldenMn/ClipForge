import json
import re
import sqlite3
from pathlib import Path

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ValidationError, field_validator

from clipforge import db as dbmod
from clipforge import diagnostic
from clipforge.api.deps import get_conn
from clipforge.models import SubtitleStyle
from clipforge.render.montage.edl import CUT_THRESHOLDS

router = APIRouter(prefix="/api/settings", tags=["settings"])

KNOWN_KEYS = set(dbmod.DEFAULT_SETTINGS)
WEIGHT_KEYS = {"weight_audio", "weight_chat", "weight_speech"}
WHISPER_MODELS = {"tiny", "base", "small", "medium", "large-v2", "large-v3",
                  "large-v3-turbo"}
CUT_STYLES = CUT_THRESHOLDS.keys()
CHANNEL_HANDLE_RE = re.compile(r"^[\w./@-]*$")


class SettingsPatch(BaseModel):
    values: dict[str, str]

    @field_validator("values")
    @classmethod
    def _validate_values(cls, v: dict[str, str]) -> dict[str, str]:
        for key, val in v.items():
            if key not in KNOWN_KEYS:
                raise ValueError(f"reglage inconnu : {key}")
            if key in WEIGHT_KEYS:
                try:
                    parsed = float(val)
                except ValueError as exc:
                    raise ValueError(f"{key} doit etre un nombre : {val}") from exc
                if parsed < 0:
                    raise ValueError(f"{key} doit etre >= 0")
            elif key == "ai_enabled" and val not in ("0", "1"):
                raise ValueError("ai_enabled doit etre '0' ou '1'")
            elif key == "output_dir" and val and not Path(val).is_absolute():
                raise ValueError(f"output_dir doit etre vide ou un chemin absolu : {val}")
            elif key == "whisper_model" and val not in WHISPER_MODELS:
                raise ValueError(
                    f"whisper_model doit etre parmi {sorted(WHISPER_MODELS)} : {val}")
            elif key == "subtitle_style_default" and val:
                # Valide a l'ECRITURE : create_clip recopie cette valeur telle
                # quelle dans le clip, et un JSON casse ne doit pas dormir dans
                # les reglages jusqu'a faire echouer un rendu.
                try:
                    SubtitleStyle(**json.loads(val))
                except (json.JSONDecodeError, ValidationError, TypeError) as exc:
                    raise ValueError(f"subtitle_style_default invalide : {exc}") from exc
            elif key == "captions_emojis" and val not in ("0", "1"):
                raise ValueError("captions_emojis doit etre '0' ou '1'")
            elif key == "montage" and val not in ("0", "1"):
                raise ValueError("montage doit etre '0' ou '1'")
            elif key == "cut_style" and val not in CUT_STYLES:
                raise ValueError(f"cut_style doit etre parmi {sorted(CUT_STYLES)} : {val}")
            elif key == "auto_clip_count":
                try:
                    parsed = int(val)
                except ValueError as exc:
                    raise ValueError(f"auto_clip_count doit etre un entier : {val}") from exc
                if not 0 <= parsed <= 10:
                    raise ValueError("auto_clip_count doit etre compris entre 0 et 10")
            elif key == "auto_profile" and len(val) > 80:
                raise ValueError("auto_profile ne doit pas depasser 80 caracteres")
            elif key == "channel_handle":
                if len(val) > 40:
                    raise ValueError("channel_handle ne doit pas depasser 40 caracteres")
                if not CHANNEL_HANDLE_RE.match(val):
                    raise ValueError(f"channel_handle invalide : {val}")
        return v


@router.get("")
def get_settings(conn: sqlite3.Connection = Depends(get_conn)):
    return {r["key"]: r["value"] for r in
            conn.execute("SELECT * FROM settings").fetchall()}


@router.patch("")
def patch_settings(body: SettingsPatch, conn: sqlite3.Connection = Depends(get_conn)):
    for k, v in body.values.items():
        dbmod.set_setting(conn, k, v)
    return get_settings(conn)


@router.get("/env")
def get_env():
    """Presence de la cle IA, jamais sa valeur.

    Passe par anthropic_key_presence() (lecture pure) et non machine_state(),
    qui sonde ffmpeg/ffprobe/GPU par subprocess — inutilement lourd pour lire
    une seule variable d'environnement.
    """
    return {"anthropic_key": diagnostic.anthropic_key_presence()}
