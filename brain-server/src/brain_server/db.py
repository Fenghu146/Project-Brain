from __future__ import annotations

import json
import os
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
  updated_at TEXT NOT NULL,
  valid_from TEXT,
  valid_until TEXT,
  branch TEXT,
  commit_hash TEXT,
  verification_due_at TEXT,
  origin TEXT DEFAULT 'user',
  superseded_by TEXT,
  revision INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS evidence (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL DEFAULT 'default',
  type TEXT NOT NULL,
  source TEXT NOT NULL,
  description TEXT,
  metadata_json TEXT,
  status TEXT NOT NULL DEFAULT 'observed',
  created_at TEXT NOT NULL,
  commit_hash TEXT,
  branch TEXT,
  locator_type TEXT DEFAULT 'absolute',
  path TEXT,
  project_root_hint TEXT,
  content_hash TEXT
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
  created_at TEXT NOT NULL,
  dedup_key TEXT,
  source TEXT,
  result TEXT
);

CREATE TABLE IF NOT EXISTS handovers (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL DEFAULT 'default',
  task_id TEXT,
  agent_id TEXT NOT NULL,
  session_id TEXT,
  status TEXT NOT NULL,
  report_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  revision INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS schema_meta (
  k TEXT PRIMARY KEY,
  v TEXT NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
  id UNINDEXED,
  type UNINDEXED,
  project_id UNINDEXED,
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
CREATE TABLE IF NOT EXISTS proposals (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  action TEXT NOT NULL,
  target_type TEXT,
  target_id TEXT,
  payload_json TEXT NOT NULL,
  reason TEXT NOT NULL,
  source_event_ids TEXT NOT NULL,
  source_evidence_ids TEXT,
  affected_ids TEXT,
  risk TEXT,
  verification_suggestion TEXT,
  confidence REAL,
  curator_version TEXT NOT NULL,
  origin TEXT NOT NULL DEFAULT 'rule_curator',
  status TEXT NOT NULL DEFAULT 'pending',
  created_at TEXT NOT NULL,
  reviewed_at TEXT,
  reviewer TEXT,
  superseded_by TEXT,
  revision INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS model_snapshots (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  basis_commit TEXT,
  basis_branch TEXT,
  generated_at TEXT NOT NULL,
  model_json TEXT NOT NULL,
  source_ids TEXT NOT NULL,
  confidence REAL,
  curator_version TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_agent_session
  ON events(agent_id, session_id);
CREATE INDEX IF NOT EXISTS idx_proposals_project_status
  ON proposals(project_id, status);
CREATE INDEX IF NOT EXISTS idx_snapshots_project_time
  ON model_snapshots(project_id, generated_at);
CREATE INDEX IF NOT EXISTS idx_events_dedup
  ON events(project_id, dedup_key);

CREATE TABLE IF NOT EXISTS sessions (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  agent_id TEXT NOT NULL,
  started_at TEXT NOT NULL,
  ended_at TEXT,
  basis_commit TEXT,
  basis_branch TEXT,
  status TEXT NOT NULL DEFAULT 'active',
  last_event_at TEXT,
  automation_mode TEXT NOT NULL DEFAULT 'full',
  metadata_json TEXT
);

CREATE TABLE IF NOT EXISTS automation_runs (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  session_id TEXT NOT NULL,
  trigger TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'running',
  started_at TEXT NOT NULL,
  finished_at TEXT,
  created_event_ids TEXT,
  created_evidence_ids TEXT,
  created_proposal_ids TEXT,
  warnings TEXT,
  error TEXT
);

CREATE TABLE IF NOT EXISTS handover_drafts (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  session_id TEXT NOT NULL,
  task_id TEXT,
  status TEXT NOT NULL DEFAULT 'pending',
  report_json TEXT NOT NULL,
  source_event_ids TEXT NOT NULL,
  proposal_ids TEXT,
  basis_commit TEXT,
  generated_by TEXT NOT NULL,
  applied_handover_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sessions_project_status
  ON sessions(project_id, status);
CREATE INDEX IF NOT EXISTS idx_automation_runs_project_session
  ON automation_runs(project_id, session_id);
CREATE INDEX IF NOT EXISTS idx_handover_drafts_project_status
  ON handover_drafts(project_id, status);

CREATE TABLE IF NOT EXISTS answer_feedback (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  question TEXT NOT NULL,
  answer_claim_ids TEXT,
  intent TEXT,
  confidence REAL,
  verdict TEXT NOT NULL,
  corrected_text TEXT,
  agent_id TEXT NOT NULL,
  session_id TEXT,
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_feedback_project_time
  ON answer_feedback(project_id, created_at);
"""


def get_connection(db_path: str | Path | None = None) -> sqlite3.Connection:
    if db_path:
        path = Path(db_path).expanduser()
    else:
        env_db = os.environ.get("BRAIN_DB_PATH")
        path = Path(env_db).expanduser() if env_db else DEFAULT_DB_PATH
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


_FTS_BIGRAM_VERSION = "1"


def _maybe_reindex_fts(conn: sqlite3.Connection) -> None:
    """Rebuild the FTS index if bigram expansion version is stale.

    The indexed `text` field for a memory depends on the version of
    `expand_chinese_bigrams`. When that function changes, the existing index
    entries become stale. We track a marker in schema_meta and re-derive all
    entries on mismatch.
    """
    try:
        cur = conn.execute("SELECT v FROM schema_meta WHERE k='fts_bigram_version'")
        row = cur.fetchone()
        current = row["v"] if row else None
    except sqlite3.OperationalError:
        current = None
    if current == _FTS_BIGRAM_VERSION:
        return
    try:
        from .models import expand_chinese_bigrams, content_to_text
        from .repository import _row_to_memory
    except Exception:
        return
    try:
        mems = conn.execute("SELECT * FROM memories").fetchall()
    except sqlite3.OperationalError:
        mems = []
    for r in mems:
        m = _row_to_memory(r)
        try:
            content = m.get("content")
            if isinstance(content, str):
                text = content
            else:
                text = content_to_text(content)
            tags = m.get("tags") or []
            if tags and isinstance(tags, str):
                try:
                    tags = json.loads(tags)
                except Exception:
                    tags = []
            fts_text = m.get("type", "") + " " + expand_chinese_bigrams(text)
            if tags:
                fts_text += " " + expand_chinese_bigrams(" ".join(tags))
            conn.execute("DELETE FROM memory_fts WHERE id=?", (m["id"],))
            conn.execute(
                "INSERT INTO memory_fts (id, type, project_id, text) VALUES (?, ?, ?, ?)",
                (m["id"], m.get("type", ""), m.get("project_id", "default"), fts_text),
            )
        except Exception:
            continue
    conn.execute(
        "INSERT OR REPLACE INTO schema_meta (k, v) VALUES ('fts_bigram_version', ?)",
        (_FTS_BIGRAM_VERSION,),
    )


def migrate(conn: sqlite3.Connection) -> None:
    _ensure_column(conn, "memories", "project_id", "project_id TEXT NOT NULL DEFAULT 'default'")
    _ensure_column(conn, "evidence", "project_id", "project_id TEXT NOT NULL DEFAULT 'default'")
    _ensure_column(conn, "events", "project_id", "project_id TEXT NOT NULL DEFAULT 'default'")
    _ensure_column(conn, "handovers", "project_id", "project_id TEXT NOT NULL DEFAULT 'default'")
    _ensure_column(conn, "links", "project_id", "project_id TEXT NOT NULL DEFAULT 'default'")
    _ensure_column(conn, "memories", "task_status", "task_status TEXT")
    for col, ddl in [
        ("valid_from", "valid_from TEXT"),
        ("valid_until", "valid_until TEXT"),
        ("branch", "branch TEXT"),
        ("commit_hash", "commit_hash TEXT"),
        ("verification_due_at", "verification_due_at TEXT"),
        ("origin", "origin TEXT DEFAULT 'user'"),
        ("superseded_by", "superseded_by TEXT"),
        ("revision", "revision INTEGER NOT NULL DEFAULT 1"),
    ]:
        _ensure_column(conn, "memories", col, ddl)
    for col, ddl in [
        ("dedup_key", "dedup_key TEXT"),
        ("source", "source TEXT"),
        ("result", "result TEXT"),
    ]:
        _ensure_column(conn, "events", col, ddl)
    for col, ddl in [
        ("commit_hash", "commit_hash TEXT"),
        ("branch", "branch TEXT"),
        ("locator_type", "locator_type TEXT DEFAULT 'absolute'"),
        ("path", "path TEXT"),
        ("project_root_hint", "project_root_hint TEXT"),
        ("content_hash", "content_hash TEXT"),
    ]:
        _ensure_column(conn, "evidence", col, ddl)
    conn.execute("CREATE TABLE IF NOT EXISTS schema_meta (k TEXT PRIMARY KEY, v TEXT NOT NULL)")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS proposals (id TEXT PRIMARY KEY, project_id TEXT NOT NULL, action TEXT NOT NULL, target_type TEXT, target_id TEXT, payload_json TEXT NOT NULL, reason TEXT NOT NULL, source_event_ids TEXT NOT NULL, source_evidence_ids TEXT, affected_ids TEXT, risk TEXT, verification_suggestion TEXT, confidence REAL, curator_version TEXT NOT NULL, origin TEXT NOT NULL DEFAULT 'rule_curator', status TEXT NOT NULL DEFAULT 'pending', created_at TEXT NOT NULL, reviewed_at TEXT, reviewer TEXT, superseded_by TEXT, revision INTEGER NOT NULL DEFAULT 1)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS model_snapshots (id TEXT PRIMARY KEY, project_id TEXT NOT NULL, basis_commit TEXT, basis_branch TEXT, generated_at TEXT NOT NULL, model_json TEXT NOT NULL, source_ids TEXT NOT NULL, confidence REAL, curator_version TEXT NOT NULL)"
    )
    # Add revision columns for concurrency control
    _ensure_column(conn, "memories", "revision", "revision INTEGER NOT NULL DEFAULT 1")
    _ensure_column(conn, "proposals", "revision", "revision INTEGER NOT NULL DEFAULT 1")
    _ensure_column(conn, "handovers", "revision", "revision INTEGER NOT NULL DEFAULT 1")
    cur = conn.execute("SELECT v FROM schema_meta WHERE k='version'")
    row = cur.fetchone()
    if row is None:
        conn.execute("INSERT OR IGNORE INTO schema_meta (k, v) VALUES ('version', '4')")
    else:
        conn.execute("UPDATE schema_meta SET v='4' WHERE k='version'")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_project_type_status ON memories(project_id, type, status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_project_task_status ON memories(project_id, task_status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_evidence_project_source ON evidence(project_id, source)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_events_project_created ON events(project_id, created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_handovers_project_created ON handovers(project_id, created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_links_project ON links(project_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_proposals_project_status ON proposals(project_id, status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_snapshots_project_time ON model_snapshots(project_id, generated_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_events_dedup ON events(project_id, dedup_key)")
    # 确保 memory_fts 有 project_id 列（虚表不可 ALTER，需重建时已在 SCHEMA 中）
    try:
        conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(id UNINDEXED, type UNINDEXED, project_id UNINDEXED, text)")
    except Exception:
        pass
    # Re-index FTS when bigram expansion changes (idempotent: checks a schema_meta flag)
    _maybe_reindex_fts(conn)
    # v0.5: sessions/automation_runs/handover_drafts tables
    conn.execute("CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, project_id TEXT NOT NULL, agent_id TEXT NOT NULL, started_at TEXT NOT NULL, ended_at TEXT, basis_commit TEXT, basis_branch TEXT, status TEXT NOT NULL DEFAULT 'active', last_event_at TEXT, automation_mode TEXT NOT NULL DEFAULT 'full', metadata_json TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS automation_runs (id TEXT PRIMARY KEY, project_id TEXT NOT NULL, session_id TEXT NOT NULL, trigger TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'running', started_at TEXT NOT NULL, finished_at TEXT, created_event_ids TEXT, created_evidence_ids TEXT, created_proposal_ids TEXT, warnings TEXT, error TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS handover_drafts (id TEXT PRIMARY KEY, project_id TEXT NOT NULL, session_id TEXT NOT NULL, task_id TEXT, status TEXT NOT NULL DEFAULT 'pending', report_json TEXT NOT NULL, source_event_ids TEXT NOT NULL, proposal_ids TEXT, basis_commit TEXT, generated_by TEXT NOT NULL, applied_handover_id TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_project_status ON sessions(project_id, status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_automation_runs_project_session ON automation_runs(project_id, session_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_handover_drafts_project_status ON handover_drafts(project_id, status)")
    conn.execute("CREATE TABLE IF NOT EXISTS answer_feedback (id TEXT PRIMARY KEY, project_id TEXT NOT NULL, question TEXT NOT NULL, answer_claim_ids TEXT, intent TEXT, confidence REAL, verdict TEXT NOT NULL, corrected_text TEXT, agent_id TEXT NOT NULL, session_id TEXT, created_at TEXT NOT NULL)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_feedback_project_time ON answer_feedback(project_id, created_at)")


def backfill_project_id(conn: sqlite3.Connection, project_id: str) -> int:
    total = 0
    for table in ("memories", "evidence", "events", "handovers", "links"):
        cur = conn.execute(f"UPDATE {table} SET project_id=? WHERE project_id='default' OR project_id IS NULL", (project_id,))
        total += cur.rowcount
    return total


def init_db(db_path: str | Path | None = None) -> sqlite3.Connection:
    conn = get_connection(db_path)
    migrated = False
    try:
        conn.executescript(SCHEMA)
    except sqlite3.OperationalError as e:
        msg = str(e).lower()
        if "no such column" in msg or "duplicate column" in msg:
            migrated = True
            migrate(conn)
        else:
            raise
    if not migrated:
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
