import sqlite3
from pathlib import Path

# Ni `segment_id` ni `vod_id` ne portent de cle etrangere, pour la meme raison :
# une cascade viderait la mesure au moment ou elle devient exploitable.
# step_segment fait DELETE FROM segments WHERE vod_id=? a chaque analyse, et le
# user supprime ses VOD pour liberer du disque (sources de plusieurs Go). Or le
# rang du candidat exporte et le signal dominant jugent l'ALGORITHME, pas la VOD :
# ils gardent tout leur sens apres sa disparition. SQLite ne sachant pas retirer
# une cle etrangere par ALTER TABLE, la reparer apres coup couterait un rebuild
# de table et les lignes deja perdues le resteraient.
USAGE_EVENTS_TABLE = """
CREATE TABLE IF NOT EXISTS usage_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL,
  vod_id INTEGER,
  segment_id INTEGER,
  rank INTEGER,
  top_signal TEXT,
  start_shift_s REAL,
  end_shift_s REAL,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
)"""

SCHEMA = """
CREATE TABLE IF NOT EXISTS vods (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL,
  source_path TEXT NOT NULL DEFAULT '',
  twitch_url TEXT, twitch_vod_id TEXT,
  duration_s REAL, width INTEGER, height INTEGER, fps REAL,
  status TEXT NOT NULL DEFAULT 'imported',
  progress REAL NOT NULL DEFAULT 0,
  error TEXT, ai_note TEXT,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);
CREATE TABLE IF NOT EXISTS words (
  vod_id INTEGER NOT NULL REFERENCES vods(id) ON DELETE CASCADE,
  idx INTEGER NOT NULL, start_s REAL NOT NULL, end_s REAL NOT NULL,
  text TEXT NOT NULL, probability REAL NOT NULL,
  PRIMARY KEY (vod_id, idx)
);
CREATE TABLE IF NOT EXISTS signals (
  vod_id INTEGER NOT NULL REFERENCES vods(id) ON DELETE CASCADE,
  kind TEXT NOT NULL, t_s REAL NOT NULL, score REAL NOT NULL,
  PRIMARY KEY (vod_id, kind, t_s)
);
CREATE TABLE IF NOT EXISTS segments (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  vod_id INTEGER NOT NULL REFERENCES vods(id) ON DELETE CASCADE,
  start_s REAL NOT NULL, end_s REAL NOT NULL, score REAL NOT NULL,
  ai_score REAL, ai_title TEXT, ai_hashtags TEXT, rank INTEGER, top_signal TEXT
);
CREATE TABLE IF NOT EXISTS scene_profiles (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE,
  cam_x INTEGER NOT NULL, cam_y INTEGER NOT NULL, cam_w INTEGER NOT NULL, cam_h INTEGER NOT NULL,
  game_x INTEGER NOT NULL, game_y INTEGER NOT NULL, game_w INTEGER NOT NULL, game_h INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS clips (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  segment_id INTEGER REFERENCES segments(id) ON DELETE SET NULL,
  vod_id INTEGER NOT NULL REFERENCES vods(id) ON DELETE CASCADE,
  start_s REAL NOT NULL, end_s REAL NOT NULL,
  title TEXT NOT NULL DEFAULT '',
  profile_id INTEGER REFERENCES scene_profiles(id) ON DELETE SET NULL,
  subtitle_style TEXT, words_json TEXT,
  status TEXT NOT NULL DEFAULT 'draft',
  progress REAL NOT NULL DEFAULT 0, output_path TEXT, error TEXT,
  origin TEXT NOT NULL DEFAULT 'user', hook TEXT
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);
""" + USAGE_EVENTS_TABLE + """;
-- Un keep concurrent (deux clics rapides sur "Garder", deux connexions du
-- threadpool FastAPI) ne doit pas pouvoir ecrire deux fois : l'unicite est
-- tranchee par SQLite lui-meme (INSERT OR IGNORE cote usage.py), pas par un
-- SELECT prealable cote application qui laisserait une fenetre entre lecture
-- et ecriture. NULL exclu : un clip auto ayant survecu a une re-analyse peut
-- avoir segment_id a NULL (cascade ON DELETE SET NULL sur segments), et rien
-- ne distingue alors deux clics sur CE clip de deux clics sur deux clips
-- differents partageant NULL — l'idempotence y reste assumee comme best-effort.
-- Pas de bump de CURRENT_SCHEMA_VERSION / MIGRATIONS : cree via executescript
-- a chaque demarrage (IF NOT EXISTS, idempotent, meme regime que le seed de
-- DEFAULT_SETTINGS ci-dessous), et aucune base reelle ne peut deja contenir de
-- ligne auto_clip_kept — les endpoints qui les ecrivent naissent dans ce meme
-- diff. Une base plus ancienne ne peut donc pas violer la contrainte a la
-- creation de l'index.
CREATE UNIQUE INDEX IF NOT EXISTS ux_usage_kept ON usage_events(segment_id)
  WHERE kind='auto_clip_kept' AND segment_id IS NOT NULL;
"""

DEFAULT_SETTINGS = {
    "weight_audio": "1.0", "weight_chat": "1.0", "weight_speech": "0.5",
    "ai_enabled": "0", "output_dir": "", "whisper_model": "large-v3",
    # Style de sous-titres appose sur les clips crees ensuite. Vide = les
    # defauts de SubtitleStyle. Seedee par INSERT OR IGNORE au demarrage, donc
    # une base existante recoit la cle sans migration de schema.
    "subtitle_style_default": "",
    "channel_handle": "", "captions_emojis": "1", "auto_clip_count": "5",
    # Moteur de montage V6 (Task 8). Cette graine fait le defaut reel en
    # production : get_db la seme avant toute lecture (main.py au demarrage,
    # queue.render_clip_job a chaque rendu). Le repli de render.montage_plan
    # (absent -> "1"/"standard") n'est qu'une garde pour une connexion qui ne
    # serait pas passee par get_db.
    "montage": "1", "cut_style": "standard",
    "auto_profile": "",
}

CURRENT_SCHEMA_VERSION = 8

# Erreurs ecrites AVANT le contrat a deux niveaux : `error` portait le message ET
# sa trace, `error_detail` restait NULL. La premiere ligne devient le message,
# tout ce qui suit descend dans le detail technique.
# Idempotente par construction : une ligne migree a un error_detail non NULL, donc
# le WHERE ne la reprend jamais. Une erreur deja au bon format (pas de saut de
# ligne, ou detail deja rempli) n'est pas touchee ; un `error` qui commence par un
# saut de ligne non plus (instr > 1), sinon le message deviendrait vide.
_BLANKS = "char(10) || char(13) || ' '"
SPLIT_LEGACY_ERRORS = f"""
UPDATE vods
   SET error_detail = trim(substr(error, instr(error, char(10)) + 1), {_BLANKS}),
       error        = trim(substr(error, 1, instr(error, char(10)) - 1), {_BLANKS})
 WHERE error_detail IS NULL
   AND instr(error, char(10)) > 1
   AND trim(substr(error, instr(error, char(10)) + 1), {_BLANKS}) <> ''
"""

MIGRATIONS = {
    2: [
        "ALTER TABLE segments ADD COLUMN signals_count INTEGER",
        "ALTER TABLE segments ADD COLUMN hook_text TEXT",
    ],
    3: [
        "ALTER TABLE vods ADD COLUMN words_complete INTEGER NOT NULL DEFAULT 0",
        ("UPDATE vods SET words_complete=1 WHERE status='ready' "
         "AND EXISTS (SELECT 1 FROM words WHERE words.vod_id = vods.id)"),
    ],
    4: [
        "ALTER TABLE vods ADD COLUMN error_detail TEXT",
        "ALTER TABLE vods ADD COLUMN step_label TEXT",
        "ALTER TABLE vods ADD COLUMN eta_s REAL",
        "ALTER TABLE vods ADD COLUMN updated_at TEXT",
    ],
    5: [SPLIT_LEGACY_ERRORS],
    6: [USAGE_EVENTS_TABLE],
    7: [
        "ALTER TABLE clips ADD COLUMN origin TEXT NOT NULL DEFAULT 'user'",
        "ALTER TABLE clips ADD COLUMN hook TEXT",
        "ALTER TABLE clips ADD COLUMN emoji TEXT",
    ],
    8: [
        ("ALTER TABLE vods ADD COLUMN profile_id INTEGER "
         "REFERENCES scene_profiles(id) ON DELETE SET NULL"),
    ],
}


def migrate(conn: sqlite3.Connection) -> None:
    """Applique les migrations manquantes, de facon idempotente.

    Les bases V1 (sans schema_version) partent de la version 1 ; les ALTER TABLE
    deja appliques sont ignores (duplicate column name).
    """
    row = conn.execute("SELECT version FROM schema_version").fetchone()
    current = row["version"] if row else 1
    for version in sorted(MIGRATIONS):
        if version <= current:
            continue
        for statement in MIGRATIONS[version]:
            try:
                conn.execute(statement)
            except sqlite3.OperationalError as exc:
                if "duplicate column name" not in str(exc).lower():
                    raise
        current = version
    conn.execute("DELETE FROM schema_version")
    conn.execute("INSERT INTO schema_version VALUES (?)", (max(current, CURRENT_SCHEMA_VERSION),))
    conn.commit()


def get_db(path: Path | str) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    for k, v in DEFAULT_SETTINGS.items():
        conn.execute("INSERT OR IGNORE INTO settings VALUES (?, ?)", (k, v))
    conn.commit()
    migrate(conn)
    return conn


def get_db_light(path: Path | str) -> sqlite3.Connection:
    """Connexion pour les requetes API : aucune ecriture d'init.

    get_db() ouvre une transaction d'ECRITURE (schema + seed) a chaque appel ;
    sous charge du pipeline (commits continus du process enfant), chaque requete
    API attendait le verrou. L'init est garantie au demarrage (lifespan).
    """
    conn = sqlite3.connect(path, timeout=5, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def create_vod(conn: sqlite3.Connection, *, title: str, source_path: str = "",
               twitch_url: str | None = None, twitch_vod_id: str | None = None) -> int:
    cur = conn.execute(
        "INSERT INTO vods (title, source_path, twitch_url, twitch_vod_id) VALUES (?,?,?,?)",
        (title, source_path, twitch_url, twitch_vod_id))
    conn.commit()
    return cur.lastrowid


def get_vod(conn: sqlite3.Connection, vod_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM vods WHERE id=?", (vod_id,)).fetchone()


TOUCH_UPDATED_AT = "updated_at=strftime('%Y-%m-%dT%H:%M:%SZ','now')"


def set_vod_status(conn: sqlite3.Connection, vod_id: int, status: str, *, progress: float = 0.0,
                   step_label: str | None = None) -> None:
    """Change d'etape : efface l'erreur precedente et l'ETA de l'etape quittee.

    Passer en erreur se fait par set_vod_error() : les deux niveaux d'erreur
    sont toujours ecrits ensemble, jamais l'un sans l'autre.
    """
    conn.execute(
        f"UPDATE vods SET status=?, progress=?, error=NULL, error_detail=NULL, "
        f"step_label=?, eta_s=NULL, {TOUCH_UPDATED_AT} WHERE id=?",
        (status, progress, step_label, vod_id))
    conn.commit()


def set_vod_error(conn: sqlite3.Connection, vod_id: int, message: str, detail: str) -> None:
    """Message court pour l'interface, detail technique pour le debogage.

    step_label est conserve : il dit a l'utilisateur ou l'analyse s'est arretee.
    """
    conn.execute(
        f"UPDATE vods SET status='error', error=?, error_detail=?, eta_s=NULL, "
        f"{TOUCH_UPDATED_AT} WHERE id=?",
        (message, detail, vod_id))
    conn.commit()


def get_setting(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else None


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute("INSERT INTO settings VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=?",
                 (key, value, value))
    conn.commit()


def auto_profile_id(conn: sqlite3.Connection) -> int | None:
    """Le profil de scene des clips automatiques, ou None.

    Le profil nomme par le reglage auto_profile s'il existe ; un nom pose dont le
    profil a disparu rend None plutot qu'un autre profil, qui cadrerait faux. Sans
    nom, l'unique profil s'il n'y a pas d'ambiguite.
    """
    nom = get_setting(conn, "auto_profile")
    if nom:
        row = conn.execute("SELECT id FROM scene_profiles WHERE name=?", (nom,)).fetchone()
        return row["id"] if row else None
    rows = conn.execute("SELECT id FROM scene_profiles LIMIT 2").fetchall()
    return rows[0]["id"] if len(rows) == 1 else None


def clip_profile_id(conn: sqlite3.Connection, vod_id: int) -> int | None:
    """Le profil qui cadre les clips de cette VOD : le sien, sinon celui du reglage.

    Une VOD importee par le pont OBS porte le cadrage de son enregistrement ;
    les autres suivent auto_profile_id. Le profil d'une VOD n'est jamais
    remplace par un autre : supprime, il retombe a NULL (ON DELETE SET NULL).
    """
    row = conn.execute("SELECT profile_id FROM vods WHERE id=?", (vod_id,)).fetchone()
    if row and row["profile_id"] is not None:
        return row["profile_id"]
    return auto_profile_id(conn)
