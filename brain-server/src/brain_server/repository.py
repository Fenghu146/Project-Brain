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


def next_id(conn: sqlite3.Connection, prefix: str, table: str, project_id: str | None = None) -> str:
    cur = conn.execute(f"SELECT id FROM {table} WHERE id LIKE ? ORDER BY id DESC LIMIT 1", (f"{prefix}-%",))
    row = cur.fetchone()
    if row is None:
        return f"{prefix}-001"
    last = row["id"]
    try:
        n = int(last.split("-")[1]) + 1
    except Exception:
        n = 1
    cand = f"{prefix}-{n:03d}"
    cur2 = conn.execute(f"SELECT 1 FROM {table} WHERE id=? LIMIT 1", (cand,))
    if cur2.fetchone() is None:
        return cand
    for _ in range(1000):
        n += 1
        cand = f"{prefix}-{n:03d}"
        cur2 = conn.execute(f"SELECT 1 FROM {table} WHERE id=? LIMIT 1", (cand,))
        if cur2.fetchone() is None:
            return cand
    return cand


def next_memory_id(conn: sqlite3.Connection, mem_type: str, project_id: str | None = None) -> str:
    prefix = MEMORY_PREFIX.get(mem_type, mem_type[0].upper())
    return next_id(conn, prefix, "memories", project_id=project_id)


def create_memory(
    conn: sqlite3.Connection,
    mem_type: str,
    content: Any,
    status: str = "draft",
    task_status: str | None = None,
    confidence: float | None = None,
    tags: list[str] | None = None,
    created_by: str = "system",
    mem_id: str | None = None,
    project_id: str = "default",
) -> str:
    if mem_id is None:
        mem_id = next_memory_id(conn, mem_type, project_id=project_id)
    ts = now_iso()
    content_json = json.dumps(content, ensure_ascii=False) if not isinstance(content, str) else json.dumps({"text": content}, ensure_ascii=False)
    tags_json = json.dumps(tags or [], ensure_ascii=False)
    conn.execute(
        "INSERT INTO memories (id, project_id, type, status, task_status, content_json, confidence, tags, created_by, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (mem_id, project_id, mem_type, status, task_status, content_json, confidence, tags_json, created_by, ts, ts),
    )
    fts_text = _fts_text(mem_type, content, tags)
    conn.execute("INSERT INTO memory_fts (id, type, text) VALUES (?,?,?)", (mem_id, mem_type, fts_text))
    return mem_id


def update_memory(
    conn: sqlite3.Connection,
    mem_id: str,
    content: Any | None = None,
    status: str | None = None,
    task_status: str | None = None,
    confidence: float | None = None,
    tags: list[str] | None = None,
    project_id: str | None = None,
) -> None:
    q = "SELECT * FROM memories WHERE id=?"
    params: list[Any] = [mem_id]
    if project_id:
        q += " AND project_id=?"
        params.append(project_id)
    cur = conn.execute(q, params)
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
    new_task_status = task_status if task_status is not None else row["task_status"]
    new_conf = confidence if confidence is not None else row["confidence"]
    ts = now_iso()
    conn.execute(
        "UPDATE memories SET content_json=?, status=?, task_status=?, confidence=?, tags=?, updated_at=? WHERE id=?",
        (new_content_json, new_status, new_task_status, new_conf, json.dumps(new_tags, ensure_ascii=False), ts, mem_id),
    )
    content_obj: Any = json.loads(new_content_json)
    fts_text = _fts_text(new_type, content_obj, new_tags)
    conn.execute("DELETE FROM memory_fts WHERE id=?", (mem_id,))
    conn.execute("INSERT INTO memory_fts (id, type, text) VALUES (?,?,?)", (mem_id, new_type, fts_text))


def get_memory(conn: sqlite3.Connection, mem_id: str, project_id: str | None = None) -> dict[str, Any] | None:
    q = "SELECT * FROM memories WHERE id=?"
    params: list[Any] = [mem_id]
    if project_id:
        q += " AND project_id=?"
        params.append(project_id)
    cur = conn.execute(q, params)
    row = cur.fetchone()
    if row is None:
        return None
    return _row_to_memory(row)


def list_memories(
    conn: sqlite3.Connection,
    project_id: str | None = None,
    mem_type: str | None = None,
    status: str | None = None,
    task_status: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    q = "SELECT * FROM memories WHERE 1=1"
    params: list[Any] = []
    if project_id:
        q += " AND project_id=?"
        params.append(project_id)
    if mem_type:
        q += " AND type=?"
        params.append(mem_type)
    if status:
        q += " AND status=?"
        params.append(status)
    if task_status:
        q += " AND task_status=?"
        params.append(task_status)
    q += " ORDER BY updated_at DESC LIMIT ?"
    params.append(limit)
    cur = conn.execute(q, params)
    return [_row_to_memory(r) for r in cur.fetchall()]


def _row_to_memory(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "project_id": row["project_id"] if "project_id" in row.keys() else "default",
        "type": row["type"],
        "status": row["status"],
        "task_status": row["task_status"] if "task_status" in row.keys() else None,
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
    project_id: str = "default",
) -> str:
    if ev_id is None:
        ev_id = next_id(conn, EVIDENCE_PREFIX, "evidence", project_id=project_id)
    ts = now_iso()
    conn.execute(
        "INSERT INTO evidence (id, project_id, type, source, description, metadata_json, status, created_at) VALUES (?,?,?,?,?,?,?,?)",
        (ev_id, project_id, ev_type, source, description, json.dumps(metadata or {}, ensure_ascii=False), status, ts),
    )
    return ev_id


def get_evidence(conn: sqlite3.Connection, ev_id: str, project_id: str | None = None) -> dict[str, Any] | None:
    q = "SELECT * FROM evidence WHERE id=?"
    params: list[Any] = [ev_id]
    if project_id:
        q += " AND project_id=?"
        params.append(project_id)
    cur = conn.execute(q, params)
    row = cur.fetchone()
    if row is None:
        return None
    return {
        "id": row["id"],
        "project_id": row["project_id"] if "project_id" in row.keys() else "default",
        "type": row["type"],
        "source": row["source"],
        "description": row["description"],
        "metadata": json.loads(row["metadata_json"]) if row["metadata_json"] else {},
        "status": row["status"],
        "created_at": row["created_at"],
    }


def list_evidence(conn: sqlite3.Connection, project_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    q = "SELECT * FROM evidence WHERE 1=1"
    params: list[Any] = []
    if project_id:
        q += " AND project_id=?"
        params.append(project_id)
    q += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    cur = conn.execute(q, params)
    return [
        {
            "id": r["id"],
            "project_id": r["project_id"] if "project_id" in r.keys() else "default",
            "type": r["type"],
            "source": r["source"],
            "description": r["description"],
            "metadata": json.loads(r["metadata_json"]) if r["metadata_json"] else {},
            "status": r["status"],
            "created_at": r["created_at"],
        }
        for r in cur.fetchall()
    ]


def create_link(conn: sqlite3.Connection, from_id: str, relation: str, to_id: str, project_id: str = "default") -> None:
    conn.execute("INSERT OR IGNORE INTO links (from_id, project_id, relation, to_id) VALUES (?,?,?,?)", (from_id, project_id, relation, to_id))


def get_links(conn: sqlite3.Connection, from_id: str | None = None, to_id: str | None = None, project_id: str | None = None) -> list[dict[str, Any]]:
    q = "SELECT * FROM links WHERE 1=1"
    params: list[Any] = []
    if project_id:
        q += " AND project_id=?"
        params.append(project_id)
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
    project_id: str = "default",
) -> str:
    if ev_id is None:
        ev_id = next_id(conn, EVENT_PREFIX, "events", project_id=project_id)
    ts = now_iso()
    conn.execute(
        "INSERT INTO events (id, project_id, action, agent_id, session_id, target, summary, payload_json, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (ev_id, project_id, action, agent_id, session_id, target, summary, json.dumps(payload or {}, ensure_ascii=False), ts),
    )
    return ev_id


def list_events(conn: sqlite3.Connection, project_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    q = "SELECT * FROM events WHERE 1=1"
    params: list[Any] = []
    if project_id:
        q += " AND project_id=?"
        params.append(project_id)
    q += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    cur = conn.execute(q, params)
    return [
        {
            "id": r["id"],
            "project_id": r["project_id"] if "project_id" in r.keys() else "default",
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
    project_id: str = "default",
) -> str:
    if handover_id is None:
        handover_id = next_id(conn, HANDOVER_PREFIX, "handovers", project_id=project_id)
    ts = now_iso()
    conn.execute(
        "INSERT INTO handovers (id, project_id, task_id, agent_id, session_id, status, report_json, created_at) VALUES (?,?,?,?,?,?,?,?)",
        (handover_id, project_id, task_id, agent_id, session_id, status, json.dumps(report, ensure_ascii=False), ts),
    )
    return handover_id


def get_handover(conn: sqlite3.Connection, handover_id: str, project_id: str | None = None) -> dict[str, Any] | None:
    q = "SELECT * FROM handovers WHERE id=?"
    params: list[Any] = [handover_id]
    if project_id:
        q += " AND project_id=?"
        params.append(project_id)
    cur = conn.execute(q, params)
    row = cur.fetchone()
    if row is None:
        return None
    return {
        "id": row["id"],
        "project_id": row["project_id"] if "project_id" in row.keys() else "default",
        "task_id": row["task_id"],
        "agent_id": row["agent_id"],
        "session_id": row["session_id"],
        "status": row["status"],
        "report": json.loads(row["report_json"]),
        "created_at": row["created_at"],
    }


def list_handovers(conn: sqlite3.Connection, project_id: str | None = None, limit: int = 10) -> list[dict[str, Any]]:
    q = "SELECT * FROM handovers WHERE 1=1"
    params: list[Any] = []
    if project_id:
        q += " AND project_id=?"
        params.append(project_id)
    q += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    cur = conn.execute(q, params)
    return [
        {
            "id": r["id"],
            "project_id": r["project_id"] if "project_id" in r.keys() else "default",
            "task_id": r["task_id"],
            "agent_id": r["agent_id"],
            "session_id": r["session_id"],
            "status": r["status"],
            "report": json.loads(r["report_json"]),
            "created_at": r["created_at"],
        }
        for r in cur.fetchall()
    ]


def _like_fallback(conn: sqlite3.Connection, query: str, limit: int, project_id: str | None = None) -> list[dict[str, Any]]:
    raw = query.replace("？", " ").replace("?", " ").replace("，", " ").replace(",", " ").strip()
    toks = [t for t in raw.split() if t.strip()]
    if not toks:
        toks = [raw]
    candidates: list[str] = []
    for t in toks:
        candidates.append(t)
        if len(t) > 4 and not t.isascii():
            for i in range(len(t) - 1):
                candidates.append(t[i : i + 2])
    seen_tok: set[str] = set()
    uniq: list[str] = []
    for c in candidates:
        if c not in seen_tok and len(c) >= 2:
            seen_tok.add(c)
            uniq.append(c)
    # AND fallback: require all tokens hit (for precise queries like "hello a secret uniqA")
    all_terms = [t for t in raw.split() if t.strip()] or [raw.strip()]
    all_like_rows: list[dict[str, Any]] = []
    if len(all_terms) >= 2 and all(t.isascii() for t in all_terms):
        q = "SELECT * FROM memories WHERE 1=1"
        params: list[Any] = []
        for t in all_terms:
            q += " AND (content_json LIKE ? OR tags LIKE ?)"
            params.extend([f"%{t}%", f"%{t}%"])
        if project_id:
            q += " AND project_id=?"
            params.append(project_id)
        q += " LIMIT ?"
        params.append(limit)
        cur = conn.execute(q, params)
        for r in cur.fetchall():
            m = _row_to_memory(r)
            all_like_rows.append(m)
        if all_like_rows:
            return all_like_rows[:limit]
    like_rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for tok in uniq[:8]:
        q = "SELECT * FROM memories WHERE (content_json LIKE ? OR tags LIKE ?)"
        params2: list[Any] = [f"%{tok}%", f"%{tok}%"]
        if project_id:
            q += " AND project_id=?"
            params2.append(project_id)
        q += " LIMIT ?"
        params2.append(limit)
        cur = conn.execute(q, params2)
        for r in cur.fetchall():
            m = _row_to_memory(r)
            if m["id"] not in seen:
                seen.add(m["id"])
                like_rows.append(m)
        if len(like_rows) >= limit:
            break
    return like_rows[:limit]


def fts_search(
    conn: sqlite3.Connection,
    query: str,
    limit: int = 20,
    project_id: str | None = None,
) -> list[dict[str, Any]]:
    if not query or not query.strip():
        return []
    fts_rows: list[dict[str, Any]] = []
    try:
        cur = conn.execute(
            "SELECT m.* FROM memory_fts f JOIN memories m ON m.id=f.id WHERE memory_fts MATCH ? ORDER BY rank LIMIT ?",
            (query, limit * 3),
        )
        fts_rows = [_row_to_memory(r) for r in cur.fetchall()]
    except sqlite3.OperationalError:
        pass
    if not fts_rows:
        try:
            cur = conn.execute(
                "SELECT m.* FROM memory_fts f JOIN memories m ON m.id=f.id WHERE memory_fts MATCH ? LIMIT ?",
                (query, limit * 3),
            )
            fts_rows = [_row_to_memory(r) for r in cur.fetchall()]
        except sqlite3.OperationalError:
            pass
    if fts_rows:
        if project_id:
            filtered = [r for r in fts_rows if r.get("project_id") == project_id]
            if filtered:
                return filtered[:limit]
            return _like_fallback(conn, query, limit, project_id=project_id)
        return fts_rows[:limit]
    return _like_fallback(conn, query, limit, project_id=project_id)


def count_memories(conn: sqlite3.Connection, project_id: str | None = None) -> int:
    if project_id:
        cur = conn.execute("SELECT COUNT(*) as c FROM memories WHERE project_id=?", (project_id,))
    else:
        cur = conn.execute("SELECT COUNT(*) as c FROM memories")
    return cur.fetchone()["c"]
