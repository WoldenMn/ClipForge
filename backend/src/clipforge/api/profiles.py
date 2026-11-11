import sqlite3

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from clipforge.api.deps import get_conn

router = APIRouter(prefix="/api/profiles", tags=["profiles"])


class ProfileIn(BaseModel):
    name: str
    cam_x: int = Field(ge=0)
    cam_y: int = Field(ge=0)
    cam_w: int = Field(gt=0)
    cam_h: int = Field(gt=0)
    game_x: int = Field(ge=0)
    game_y: int = Field(ge=0)
    game_w: int = Field(gt=0)
    game_h: int = Field(gt=0)


@router.get("")
def list_profiles(conn: sqlite3.Connection = Depends(get_conn)):
    return [dict(r) for r in conn.execute(
        "SELECT * FROM scene_profiles ORDER BY name").fetchall()]


@router.post("")
def upsert_profile(body: ProfileIn, conn: sqlite3.Connection = Depends(get_conn)):
    conn.execute(
        "INSERT INTO scene_profiles (name, cam_x, cam_y, cam_w, cam_h, game_x, game_y, "
        "game_w, game_h) VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(name) DO UPDATE SET "
        "cam_x=excluded.cam_x, cam_y=excluded.cam_y, cam_w=excluded.cam_w, "
        "cam_h=excluded.cam_h, game_x=excluded.game_x, game_y=excluded.game_y, "
        "game_w=excluded.game_w, game_h=excluded.game_h",
        (body.name, body.cam_x, body.cam_y, body.cam_w, body.cam_h,
         body.game_x, body.game_y, body.game_w, body.game_h))
    conn.commit()
    row = conn.execute("SELECT * FROM scene_profiles WHERE name=?", (body.name,)).fetchone()
    return dict(row)


@router.delete("/{profile_id}")
def delete_profile(profile_id: int, conn: sqlite3.Connection = Depends(get_conn)):
    if not conn.execute("SELECT 1 FROM scene_profiles WHERE id=?", (profile_id,)).fetchone():
        raise HTTPException(404)
    conn.execute("DELETE FROM scene_profiles WHERE id=?", (profile_id,))
    conn.commit()
    return {"deleted": True}
