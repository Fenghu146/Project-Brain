from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .db import get_connection
from .models import MEMORY_PREFIX, EVIDENCE_PREFIX, EVENT_PREFIX, HANDOVER_PREFIX, now_iso, content_to_text


def _fts_text(mem_type: str, content: Any, tags: list[str] | None) -> str:
    parts = [mem_type, content_to_text(content)]
    if tags:
        parts.append(" ".join(tags))
    return " ".join(parts)


def next_id(conn: sqlite3.Connection, prefix: str, table: str) -> str:
    cur = conn.execute(f"SELECT id FROM {table} WHERE id LIKE ? ORDER BY id DESC LIMIT 1", (f"{prefix}-%",))
    row = cur.fetchone()
    if row is None:
        return f"{prefix}-001"
    last = row["id"]
    try:
        n = int(last.split("-")[1]) + 1
    except Exception:
        n = 1
    return f"{prefix}-{n:03d}"


def next_memory_id(conn: sqlite3.Connection, mem_type: str) -> str:
    prefix = MEMORY_PREFIX.get(mem_type, mem_type[0].upper())
    return next_id(conn, prefix, "memories")


def create_memory(
    conn: sqlite3.Connection,
    mem_type: str,
    content: Any,
    status: str = "draft",
    confidence: float | None = None,
    tags: list[str] | None = None,
    created_by: str = "system",
    mem_id: str | None = None,
) -> str:
    if mem_id is None:
        mem_id = next_memory_id(conn, mem_type)
    ts = now_iso()
    content_json = json.dumps(content, ensure_ascii=False) if not isinstance(content, str) else json.dumps({"text": content}, ensure_ascii=False)
    tags_json = json.dumps(tags or [], ensure_ascii=False)
    conn.execute(
        "INSERT INTO memories (id, type, status, content_json, confidence, tags, created_by, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (mem_id, mem_type, status, content_json, confidence, tags_json, created_by, ts, ts),
    )
    fts_text = _fts_text(mem_type, content, tags)
    conn.execute("INSERT INTO memory_fts (id, type, text) VALUES (?,?,?)", (mem_id, mem_type, fts_text))
    return mem_id


def update_memory(
    conn: sqlite3.Connection,
    mem_id: str,
    content: Any | None = None,
    status: str | None = None,
    confidence: float | None = None,
    tags: list[str] | None = None,
) -> None:
    cur = conn.execute("SELECT * FROM memories WHERE id=?", (mem_id,))
    row = cur.fetchone()
    if row is None:
        raise KeyError(f"memory not found: {mem_id}")
    new_content_json = row["content_json"]
    new_type = row["type"]
    new_tags = json.loads(row["tags"]) if row["tags"] else []
    if content is not None:
        new_content_json = json.dumps(content, ensure_ascii=False) if not isinstance(content, str) else json.dumps({"text": content}, ensure_ascii=False)
    if tags is not None:
        new_tags = tags
    new_status = status or row["status"]
    new_conf = confidence if confidence is not None else row["confidence"]
    ts = now_iso()
    conn.execute(
        "UPDATE memories SET content_json=?, status=?, confidence=?, tags=?, updated_at=? WHERE id=?",
        (new_content_json, new_status, new_conf, json.dumps(new_tags, ensure_ascii=False), ts, mem_id),
    )
    content_obj: Any = json.loads(new_content_json)
    fts_text = _fts_text(new_type, content_obj, new_tags)
    conn.execute("DELETE FROM memory_fts WHERE id=?", (mem_id,))
    conn.execute("INSERT INTO memory_fts (id, type, text) VALUES (?,?,?)", (mem_id, new_type, fts_text))


def get_memory(conn: sqlite3.Connection, mem_id: str) -> dict[str, Any] | None:
    cur = conn.execute("SELECT * FROM memories WHERE id=?", (mem_id,))
    row = cur.fetchone()
    if row is None:
        return None
    return _row_to_memory(row)


def list_memories(
    conn: sqlite3.Connection,
    mem_type: str | None = None,
    status: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    q = "SELECT * FROM memories WHERE 1=1"
    params: list[Any] = []
    if mem_type:
        q += " AND type=?"
        params.append(mem_type)
    if status:
        q += " AND status=?"
        params.append(status)
    q += " ORDER BY updated_at DESC LIMIT ?"
    params.append(limit)
    cur = conn.execute(q, params)
    return [_row_to_memory(r) for r in cur.fetchall()]


def _row_to_memory(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "type": row["type"],
        "status": row["status"],
        "content": json.loads(row["content_json"]),
        "confidence": row["confidence"],
        "tags": json.loads(row["tags"]) if row["tags"] else [],
        "created_by": row["created_by"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def create_evidence(
    conn: sqlite3.Connection,
    ev_type: str,
    source: str,
    description: str | None = None,
    metadata: dict[str, Any] | None = None,
    status: str = "observed",
    ev_id: str | None = None,
) -> str:
    if ev_id is None:
        ev_id = next_id(conn, EVIDENCE_PREFIX, "evidence")
    ts = now_iso()
    conn.execute(
        "INSERT INTO evidence (id, type, source, description, metadata_json, status, created_at) VALUES (?,?,?,?,?,?,?)",
        (ev_id, ev_type, source, description, json.dumps(metadata or {}, ensure_ascii=False), status, ts),
    )
    return ev_id


def get_evidence(conn: sqlite3.Connection, ev_id: str) -> dict[str, Any] | None:
    cur = conn.execute("SELECT * FROM evidence WHERE id=?", (ev_id,))
    row = cur.fetchone()
    if row is None:
        return None
    return {
        "id": row["id"],
        "type": row["type"],
        "source": row["source"],
        "description": row["description"],
        "metadata": json.loads(row["metadata_json"]) if row["metadata_json"] else {},
        "status": row["status"],
        "created_at": row["created_at"],
    }


def list_evidence(conn: sqlite3.Connection, limit: int = 50) -> list[dict[str, Any]]:
    cur = conn.execute("SELECT * FROM evidence ORDER BY created_at DESC LIMIT ?", (limit,))
    return [
        {
            "id": r["id"],
            "type": r["type"],
            "source": r["source"],
            "description": r["description"],
            "metadata": json.loads(r["metadata_json"]) if r["metadata_json"] else {},
            "status": r["status"],
            "created_at": r["created_at"],
        }
        for r in cur.fetchall()
    ]


def create_link(conn: sqlite3.Connection, from_id: str, relation: str, to_id: str) -> None:
    conn.execute("INSERT OR IGNORE INTO links (from_id, relation, to_id) VALUES (?,?,?)", (from_id, relation, to_id))


def get_links(conn: sqlite3.Connection, from_id: str | None = None, to_id: str | None = None) -> list[dict[str, Any]]:
    q = "SELECT * FROM links WHERE 1=1"
    params: list[Any] = []
    if from_id:
        q += " AND from_id=?"
        params.append(from_id)
    if to_id:
        q += " AND to_id=?"
        params.append(to_id)
    cur = conn.execute(q, params)
    return [dict(r) for r in cur.fetchall()]


def create_event(
    conn: sqlite3.Connection,
    action: str,
    agent_id: str,
    session_id: str | None = None,
    target: str | None = None,
    summary: str | None = None,
    payload: dict[str, Any] | None = None,
    ev_id: str | None = None,
) -> str:
    if ev_id is None:
        ev_id = next_id(conn, EVENT_PREFIX, "events")
    ts = now_iso()
    conn.execute(
        "INSERT INTO events (id, action, agent_id, session_id, target, summary, payload_json, created_at) VALUES (?,?,?,?,?,?,?,?)",
        (ev_id, action, agent_id, session_id, target, summary, json.dumps(payload or {}, ensure_ascii=False), ts),
    )
    return ev_id


def list_events(conn: sqlite3.Connection, limit: int = 50) -> list[dict[str, Any]]:
    cur = conn.execute("SELECT * FROM events ORDER BY created_at DESC LIMIT ?", (limit,))
    return [
        {
            "id": r["id"],
            "action": r["action"],
            "agent_id": r["agent_id"],
            "session_id": r["session_id"],
            "target": r["target"],
            "summary": r["summary"],
            "payload": json.loads(r["payload_json"]) if r["payload_json"] else {},
            "created_at": r["created_at"],
        }
        for r in cur.fetchall()
    ]


def create_handover(
    conn: sqlite3.Connection,
    task_id: str | None,
    agent_id: str,
    session_id: str | None,
    status: str,
    report: dict[str, Any],
    handover_id: str | None = None,
) -> str:
    if handover_id is None:
        handover_id = next_id(conn, HANDOVER_PREFIX, "handovers")
    ts = now_iso()
    conn.execute(
        "INSERT INTO handovers (id, task_id, agent_id, session_id, status, report_json, created_at) VALUES (?,?,?,?,?,?,?)",
        (handover_id, task_id, agent_id, session_id, status, json.dumps(report, ensure_ascii=False), ts),
    )
    return handover_id


def get_handover(conn: sqlite3.Connection, handover_id: str) -> dict[str, Any] | None:
    cur = conn.execute("SELECT * FROM handovers WHERE id=?", (handover_id,))
    row = cur.fetchone()
    if row is None:
        return None
    return {
        "id": row["id"],
        "task_id": row["task_id"],
        "agent_id": row["agent_id"],
        "session_id": row["session_id"],
        "status": row["status"],
        "report": json.loads(row["report_json"]),
        "created_at": row["created_at"],
    }


def fts_search(
    conn: sqlite3.Connection,
    query: str,
    limit: int = 20,
) -> list[dict[str, Any]]:
    if not query or not query.strip():
        return []
    try:
        cur = conn.execute(
            "SELECT m.* FROM memory_fts f JOIN memories m ON m.id=f.id WHERE memory_fts MATCH ? ORDER BY rank LIMIT ?",
            (query, limit),
        )
        rows = [_row_to_memory(r) for r in cur.fetchall()]
    except sqlite3.OperationalError:
        cur = conn.execute(
            "SELECT m.* FROM memory_fts f JOIN memories m ON m.id=f.id WHERE memory_fts MATCH ? LIMIT ?",
            (query, limit),
        )
        rows = [_row_to_memory(r) for r in cur.fetchall()]
    if rows:
        return rows
    toks = [t for t in query.replace("？", " ").replace("?", " ").replace("，", " ").replace(",", " ").split() if t.strip()]
    if not toks:
        toks = [query.strip()]
    like_rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for tok in toks[:4]:
        cur = conn.execute("SELECT * FROM memories WHERE content_json LIKE ? OR tags LIKE ? LIMIT ?", (f"%{tok}%", f"%{tok}%", limit))
        for r in cur.fetchall():
            m = _row_to_memory(r)
            if m["id"] not in seen:
                seen.add(m["id"])
                like_rows.append(m)
        if len(like_rows) >= limit:
            break
    return like_rows[:limit]


def count_memories(conn: sqlite3.Connection) -> int:
    cur = conn.execute("SELECT COUNT(*) as c FROM memories")
    return cur.fetchone()["c"]
