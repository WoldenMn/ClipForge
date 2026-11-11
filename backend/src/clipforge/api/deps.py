import sqlite3
from collections.abc import Iterator

from clipforge import config
from clipforge import db as dbmod


def get_conn() -> Iterator[sqlite3.Connection]:
    conn = dbmod.get_db_light(config.DB_PATH)
    try:
        yield conn
    finally:
        conn.close()
