from __future__ import annotations

import json
import sqlite3
from pathlib import Path

DEFAULT_DB_PATH = Path(__file__).parents[3].joinpath(".brain/brain.db").resolve()

SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS memories (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL DEFAULT 'default',
  type TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'draft',
  task_status TEXT,
  content_json TEXT NOT NULL,
  confidence REAL,
  tags TEXT,
  created_by TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS evidence (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL DEFAULT 'default',
  type TEXT NOT NULL,
  source TEXT NOT NULL,
  description TEXT,
  metadata_json TEXT,
  status TEXT NOT NULL DEFAULT 'observed',
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS links (
  from_id TEXT NOT NULL,
  project_id TEXT NOT NULL DEFAULT 'default',
  relation TEXT NOT NULL,
  to_id TEXT NOT NULL,
  PRIMARY KEY (from_id, relation, to_id)
);

CREATE TABLE IF NOT EXISTS events (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL DEFAULT 'default',
  action TEXT NOT NULL,
  agent_id TEXT NOT NULL,
  session_id TEXT,
  target TEXT,
  summary TEXT,
  payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS handovers (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL DEFAULT 'default',
  task_id TEXT,
  agent_id TEXT NOT NULL,
  session_id TEXT,
  status TEXT NOT NULL,
  report_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS schema_meta (
  k TEXT PRIMARY KEY,
  v TEXT NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
  id UNINDEXED,
  type UNINDEXED,
  text
);

CREATE INDEX IF NOT EXISTS idx_memories_project_type_status
  ON memories(project_id, type, status);
CREATE INDEX IF NOT EXISTS idx_memories_project_task_status
  ON memories(project_id, task_status);
CREATE INDEX IF NOT EXISTS idx_evidence_project_source
  ON evidence(project_id, source);
CREATE INDEX IF NOT EXISTS idx_events_project_created
  ON events(project_id, created_at);
CREATE INDEX IF NOT EXISTS idx_handovers_project_created
  ON handovers(project_id, created_at);
CREATE INDEX IF NOT EXISTS idx_links_project
  ON links(project_id);
CREATE INDEX IF NOT EXISTS idx_events_agent_session
  ON events(agent_id, session_id);
"""


def get_connection(db_path: str | Path | None = None) -> sqlite3.Connection:
    path = Path(db_path) if db_path else DEFAULT_DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    cur = conn.execute(f"PRAGMA table_info({table})")
    return {r["name"] for r in cur.fetchall()}


def _ensure_column(conn: sqlite3.Connection, table: str, col: str, ddl: str) -> None:
    if col not in _columns(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")


def migrate(conn: sqlite3.Connection) -> None:
    _ensure_column(conn, "memories", "project_id", "project_id TEXT NOT NULL DEFAULT 'default'")
    _ensure_column(conn, "evidence", "project_id", "project_id TEXT NOT NULL DEFAULT 'default'")
    _ensure_column(conn, "events", "project_id", "project_id TEXT NOT NULL DEFAULT 'default'")
    _ensure_column(conn, "handovers", "project_id", "project_id TEXT NOT NULL DEFAULT 'default'")
    _ensure_column(conn, "links", "project_id", "project_id TEXT NOT NULL DEFAULT 'default'")
    _ensure_column(conn, "memories", "task_status", "task_status TEXT")
    conn.execute("CREATE TABLE IF NOT EXISTS schema_meta (k TEXT PRIMARY KEY, v TEXT NOT NULL)")
    cur = conn.execute("SELECT v FROM schema_meta WHERE k='version'")
    row = cur.fetchone()
    if row is None:
        conn.execute("INSERT OR IGNORE INTO schema_meta (k, v) VALUES ('version', '2')")
    else:
        conn.execute("UPDATE schema_meta SET v='2' WHERE k='version'")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_project_type_status ON memories(project_id, type, status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_project_task_status ON memories(project_id, task_status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_evidence_project_source ON evidence(project_id, source)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_events_project_created ON events(project_id, created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_handovers_project_created ON handovers(project_id, created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_links_project ON links(project_id)")
    conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(id UNINDEXED, type UNINDEXED, text)")


def backfill_project_id(conn: sqlite3.Connection, project_id: str) -> int:
    total = 0
    for table in ("memories", "evidence", "events", "handovers", "links"):
        cur = conn.execute(f"UPDATE {table} SET project_id=? WHERE project_id='default' OR project_id IS NULL", (project_id,))
        total += cur.rowcount
    return total


def init_db(db_path: str | Path | None = None) -> sqlite3.Connection:
    conn = get_connection(db_path)
    try:
        conn.executescript(SCHEMA)
    except sqlite3.OperationalError as e:
        if "no such column: project_id" not in str(e) and "duplicate column" not in str(e).lower():
            raise
    migrate(conn)
    cfg_path: Path | None = None
    if db_path:
        cfg_path = Path(str(db_path)).parent / "config.json"
    else:
        cfg_path = Path(__file__).parents[3] / ".brain/config.json"
    project_id = None
    if cfg_path and cfg_path.exists():
        try:
            project_id = json.loads(cfg_path.read_text(encoding="utf-8")).get("project_id")
        except Exception:
            project_id = None
    if project_id:
        cur = conn.execute("SELECT count(*) as c FROM memories WHERE project_id='default'")
        if cur.fetchone()["c"] > 0:
            backfill_project_id(conn, project_id)
            conn.commit()
            return conn
        cur = conn.execute("SELECT count(*) as c FROM memories")
        if cur.fetchone()["c"] == 0:
            cur2 = conn.execute("SELECT count(*) as c FROM schema_meta WHERE k='seed_project'")
            if cur2.fetchone()["c"] == 0:
                conn.execute("INSERT OR IGNORE INTO schema_meta (k, v) VALUES ('seed_project', ?)", (project_id,))
    conn.commit()
    return conn
