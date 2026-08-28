from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .db import get_connection
from .models import MEMORY_PREFIX, EVIDENCE_PREFIX, EVENT_PREFIX, HANDOVER_PREFIX, now_iso, content_to_text, ConflictError


def _fts_text(mem_type: str, content: Any, tags: list[str] | None) -> str:
    parts = [mem_type, content_to_text(content)]
    if tags:
        parts.append(" ".join(tags))
    return " ".join(parts)


def _fts_upsert(conn: sqlite3.Connection, mem_id: str, mem_type: str, text: str, project_id: str) -> None:
    """Insert or replace FTS entry for a memory."""
    try:
        conn.execute("INSERT INTO memory_fts (id, type, project_id, text) VALUES (?, ?, ?, ?)",
                     (mem_id, mem_type, project_id, text))
    except Exception:
        # For FTS5, use DELETE + INSERT as REPLACE may not work reliably
        conn.execute("DELETE FROM memory_fts WHERE id=?", (mem_id,))
        conn.execute("INSERT INTO memory_fts (id, type, project_id, text) VALUES (?, ?, ?, ?)",
                     (mem_id, mem_type, project_id, text))


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
    valid_from: str | None = None,
    valid_until: str | None = None,
    branch: str | None = None,
    commit_hash: str | None = None,
    verification_due_at: str | None = None,
    origin: str = "user",
) -> str:
    if mem_id is None:
        mem_id = next_memory_id(conn, mem_type, project_id=project_id)
    ts = now_iso()
    content_json = json.dumps(content, ensure_ascii=False) if not isinstance(content, str) else json.dumps({"text": content}, ensure_ascii=False)
    tags_json = json.dumps(tags or [], ensure_ascii=False)
    conn.execute(
        "INSERT INTO memories (id, project_id, type, status, task_status, content_json, confidence, tags, created_by, created_at, updated_at, valid_from, valid_until, branch, commit_hash, verification_due_at, origin) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (mem_id, project_id, mem_type, status, task_status, content_json, confidence, tags_json, created_by, ts, ts, valid_from, valid_until, branch, commit_hash, verification_due_at, origin),
    )
    fts_text = _fts_text(mem_type, content, tags)
    _fts_upsert(conn, mem_id, mem_type, fts_text, project_id)
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
    expected_revision: int | None = None,
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
    # Optimistic lock check
    current_revision = row["revision"] if "revision" in row.keys() else 1
    if expected_revision is not None and current_revision != expected_revision:
        raise ConflictError(
            f"memory {mem_id} has been modified (revision {current_revision}, expected {expected_revision})"
        )
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
    new_revision = current_revision + 1
    conn.execute(
        "UPDATE memories SET content_json=?, status=?, task_status=?, confidence=?, tags=?, updated_at=?, revision=? WHERE id=?",
        (new_content_json, new_status, new_task_status, new_conf, json.dumps(new_tags, ensure_ascii=False), ts, new_revision, mem_id),
    )
    content_obj: Any = json.loads(new_content_json)
    fts_text = _fts_text(new_type, content_obj, new_tags)
    _fts_upsert(conn, mem_id, new_type, fts_text, row["project_id"])


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
        "valid_from": row["valid_from"] if "valid_from" in row.keys() else None,
        "valid_until": row["valid_until"] if "valid_until" in row.keys() else None,
        "branch": row["branch"] if "branch" in row.keys() else None,
        "commit_hash": row["commit_hash"] if "commit_hash" in row.keys() else None,
        "verification_due_at": row["verification_due_at"] if "verification_due_at" in row.keys() else None,
        "origin": row["origin"] if "origin" in row.keys() else "user",
        "superseded_by": row["superseded_by"] if "superseded_by" in row.keys() else None,
        "revision": row["revision"] if "revision" in row.keys() else 1,
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
    locator_type: str = "absolute",
    path: str | None = None,
    project_root_hint: str | None = None,
    content_hash: str | None = None,
) -> str:
    if ev_id is None:
        ev_id = next_id(conn, EVIDENCE_PREFIX, "evidence", project_id=project_id)
    ts = now_iso()
    conn.execute(
        "INSERT INTO evidence (id, project_id, type, source, description, metadata_json, status, created_at, locator_type, path, project_root_hint, content_hash) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (ev_id, project_id, ev_type, source, description, json.dumps(metadata or {}, ensure_ascii=False), status, ts, locator_type, path, project_root_hint, content_hash),
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
        "locator_type": row["locator_type"] if "locator_type" in row.keys() and row["locator_type"] else "absolute",
        "path": row["path"] if "path" in row.keys() else None,
        "project_root_hint": row["project_root_hint"] if "project_root_hint" in row.keys() else None,
        "content_hash": row["content_hash"] if "content_hash" in row.keys() else None,
        "commit_hash": row["commit_hash"] if "commit_hash" in row.keys() else None,
        "branch": row["branch"] if "branch" in row.keys() else None,
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
            "locator_type": r["locator_type"] if "locator_type" in r.keys() and r["locator_type"] else "absolute",
            "path": r["path"] if "path" in r.keys() else None,
            "project_root_hint": r["project_root_hint"] if "project_root_hint" in r.keys() else None,
            "content_hash": r["content_hash"] if "content_hash" in r.keys() else None,
            "commit_hash": r["commit_hash"] if "commit_hash" in r.keys() else None,
            "branch": r["branch"] if "branch" in r.keys() else None,
        }
        for r in cur.fetchall()
    ]


def create_link(conn: sqlite3.Connection, from_id: str, relation: str, to_id: str, project_id: str = "default") -> None:
    conn.execute("INSERT OR IGNORE INTO links (from_id, project_id, relation, to_id) VALUES (?,?,?,?)", (from_id, project_id, relation, to_id))


def resolve_evidence_path(ev: dict[str, Any], project_root: Path | None = None) -> tuple[str, str]:
    """Resolve evidence path, return (resolved_path, health_status).
    
    Resolution priority:
    1. Relative path from project_root_hint
    2. Relative path from configured project_root
    3. Git blob via commit_hash + path
    4. Original absolute source (diagnostic only)
    """
    locator_type = ev.get("locator_type", "absolute")
    path = ev.get("path") or ev.get("source", "")
    commit_hash = ev.get("commit_hash")
    
    # git_blob type
    if locator_type == "git_blob" and commit_hash and path:
        return f"git:{commit_hash}:{path}", "reachable"
    
    # project_relative type
    if locator_type == "project_relative" and path:
        # Try project_root_hint first
        hint = ev.get("project_root_hint")
        if hint:
            candidate = Path(hint) / path
            if candidate.exists():
                return str(candidate), "reachable"
        
        # Try configured project_root
        if project_root:
            candidate = project_root / path
            if candidate.exists():
                return str(candidate), "reachable"
        
        # Fall back to source for diagnosis
        return path, "moved"
    
    # absolute or unknown - check if exists
    if path:
        abs_path = Path(path)
        if abs_path.exists():
            return str(abs_path), "reachable"
        return path, "missing"
    
    return path or ev.get("source", "unknown"), "unknown"


def check_evidence_health(conn: sqlite3.Connection, project_id: str) -> list[dict[str, Any]]:
    """Check health of all evidence in a project."""
    evidences = list_evidence(conn, project_id=project_id, limit=1000)
    results = []
    for ev in evidences:
        resolved_path, status = resolve_evidence_path(ev)
        results.append({
            "id": ev["id"],
            "type": ev["type"],
            "source": ev["source"],
            "locator_type": ev.get("locator_type", "absolute"),
            "path": ev.get("path"),
            "resolved_path": resolved_path,
            "health": status,
            "commit_hash": ev.get("commit_hash"),
        })
    return results


PROPOSAL_PREFIX = "P"
SNAPSHOT_PREFIX = "MS"


def create_proposal(
    conn: sqlite3.Connection,
    project_id: str,
    action: str,
    reason: str,
    source_event_ids: list[str],
    payload: dict[str, Any] | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    source_evidence_ids: list[str] | None = None,
    affected_ids: list[str] | None = None,
    risk: str | None = None,
    verification_suggestion: str | None = None,
    confidence: float | None = None,
    curator_version: str = "rule-v1",
    origin: str = "rule_curator",
    proposal_id: str | None = None,
) -> str:
    if proposal_id is None:
        proposal_id = next_id(conn, PROPOSAL_PREFIX, "proposals")
    ts = now_iso()
    conn.execute(
        "INSERT INTO proposals (id, project_id, action, target_type, target_id, payload_json, reason, source_event_ids, source_evidence_ids, affected_ids, risk, verification_suggestion, confidence, curator_version, origin, status, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (proposal_id, project_id, action, target_type, target_id, json.dumps(payload or {}, ensure_ascii=False), reason, json.dumps(source_event_ids, ensure_ascii=False), json.dumps(source_evidence_ids or [], ensure_ascii=False), json.dumps(affected_ids or [], ensure_ascii=False), risk, verification_suggestion, confidence, curator_version, origin, "pending", ts),
    )
    return proposal_id


def get_proposal(conn: sqlite3.Connection, proposal_id: str, project_id: str | None = None) -> dict[str, Any] | None:
    q = "SELECT * FROM proposals WHERE id=?"
    params: list[Any] = [proposal_id]
    if project_id:
        q += " AND project_id=?"
        params.append(project_id)
    cur = conn.execute(q, params)
    row = cur.fetchone()
    if row is None:
        return None
    return _row_to_proposal(row)


def _row_to_proposal(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "project_id": row["project_id"],
        "action": row["action"],
        "target_type": row["target_type"],
        "target_id": row["target_id"],
        "payload": json.loads(row["payload_json"]) if row["payload_json"] else {},
        "reason": row["reason"],
        "source_event_ids": json.loads(row["source_event_ids"]) if row["source_event_ids"] else [],
        "source_evidence_ids": json.loads(row["source_evidence_ids"]) if row["source_evidence_ids"] else [],
        "affected_ids": json.loads(row["affected_ids"]) if row["affected_ids"] else [],
        "risk": row["risk"],
        "verification_suggestion": row["verification_suggestion"],
        "confidence": row["confidence"],
        "curator_version": row["curator_version"],
        "origin": row["origin"],
        "status": row["status"],
        "created_at": row["created_at"],
        "reviewed_at": row["reviewed_at"],
        "reviewer": row["reviewer"],
        "superseded_by": row["superseded_by"],
    }


def list_proposals(conn: sqlite3.Connection, project_id: str | None = None, status: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
    q = "SELECT * FROM proposals WHERE 1=1"
    params: list[Any] = []
    if project_id:
        q += " AND project_id=?"
        params.append(project_id)
    if status:
        q += " AND status=?"
        params.append(status)
    q += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    cur = conn.execute(q, params)
    return [_row_to_proposal(r) for r in cur.fetchall()]


def update_proposal(conn: sqlite3.Connection, proposal_id: str, status: str, reviewer: str | None = None, reason: str | None = None, superseded_by: str | None = None, project_id: str | None = None, expected_revision: int | None = None) -> None:
    q = "SELECT * FROM proposals WHERE id=?"
    params: list[Any] = [proposal_id]
    if project_id:
        q += " AND project_id=?"
        params.append(project_id)
    cur = conn.execute(q, params)
    row = cur.fetchone()
    if row is None:
        raise KeyError(f"proposal not found: {proposal_id}")
    if row["status"] == "approved":
        raise ValueError(f"proposal {proposal_id} already approved, cannot re-apply")
    # Optimistic lock check
    current_revision = row["revision"] if "revision" in row.keys() else 1
    if expected_revision is not None and current_revision != expected_revision:
        raise ConflictError(
            f"proposal {proposal_id} has been modified (revision {current_revision}, expected {expected_revision})"
        )
    ts = now_iso()
    new_revision = current_revision + 1
    conn.execute("UPDATE proposals SET status=?, reviewed_at=?, reviewer=?, superseded_by=?, revision=? WHERE id=?", (status, ts, reviewer, superseded_by, new_revision, proposal_id))


def create_snapshot(conn: sqlite3.Connection, project_id: str, model_json: dict[str, Any], source_ids: list[str], basis_commit: str | None = None, basis_branch: str | None = None, confidence: float | None = None, curator_version: str = "rule-v1", snapshot_id: str | None = None) -> str:
    if snapshot_id is None:
        snapshot_id = next_id(conn, SNAPSHOT_PREFIX, "model_snapshots")
    ts = now_iso()
    conn.execute("INSERT INTO model_snapshots (id, project_id, basis_commit, basis_branch, generated_at, model_json, source_ids, confidence, curator_version) VALUES (?,?,?,?,?,?,?,?,?)", (snapshot_id, project_id, basis_commit, basis_branch, ts, json.dumps(model_json, ensure_ascii=False), json.dumps(source_ids, ensure_ascii=False), confidence, curator_version))
    return snapshot_id


def get_snapshot(conn: sqlite3.Connection, snapshot_id: str, project_id: str | None = None) -> dict[str, Any] | None:
    q = "SELECT * FROM model_snapshots WHERE id=?"
    params: list[Any] = [snapshot_id]
    if project_id:
        q += " AND project_id=?"
        params.append(project_id)
    cur = conn.execute(q, params)
    row = cur.fetchone()
    if row is None:
        return None
    return {"id": row["id"], "project_id": row["project_id"], "basis_commit": row["basis_commit"], "basis_branch": row["basis_branch"], "generated_at": row["generated_at"], "model_json": json.loads(row["model_json"]), "source_ids": json.loads(row["source_ids"]), "confidence": row["confidence"], "curator_version": row["curator_version"]}


def list_snapshots(conn: sqlite3.Connection, project_id: str | None = None, limit: int = 10) -> list[dict[str, Any]]:
    q = "SELECT * FROM model_snapshots WHERE 1=1"
    params: list[Any] = []
    if project_id:
        q += " AND project_id=?"
        params.append(project_id)
    q += " ORDER BY generated_at DESC LIMIT ?"
    params.append(limit)
    cur = conn.execute(q, params)
    return [{"id": r["id"], "project_id": r["project_id"], "basis_commit": r["basis_commit"], "basis_branch": r["basis_branch"], "generated_at": r["generated_at"], "model_json": json.loads(r["model_json"]), "source_ids": json.loads(r["source_ids"]), "confidence": r["confidence"], "curator_version": r["curator_version"]} for r in cur.fetchall()]


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
    dedup_key: str | None = None,
    source: str | None = None,
    result: str | None = None,
) -> str:
    if dedup_key:
        cur = conn.execute("SELECT id FROM events WHERE project_id=? AND dedup_key=? LIMIT 1", (project_id, dedup_key))
        row = cur.fetchone()
        if row:
            return row["id"]
    if ev_id is None:
        ev_id = next_id(conn, EVENT_PREFIX, "events", project_id=project_id)
    ts = now_iso()
    conn.execute(
        "INSERT INTO events (id, project_id, action, agent_id, session_id, target, summary, payload_json, created_at, dedup_key, source, result) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (ev_id, project_id, action, agent_id, session_id, target, summary, json.dumps(payload or {}, ensure_ascii=False), ts, dedup_key, source, result),
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
            "dedup_key": r["dedup_key"] if "dedup_key" in r.keys() else None,
            "source": r["source"] if "source" in r.keys() else None,
            "result": r["result"] if "result" in r.keys() else None,
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
) -> list[dict[str, Any]] | tuple[list[dict[str, Any]], dict[str, int]]:
    """FTS search with project isolation."""
    if not query or not query.strip():
        return []

    fts_rows: list[dict[str, Any]] = []

    try:
        cur = conn.execute(
            "SELECT m.* FROM memory_fts f JOIN memories m ON m.id=f.id "
            "WHERE f.project_id=? AND memory_fts MATCH ? ORDER BY rank LIMIT ?",
            (project_id or "default", query, limit * 3),
        )
        fts_rows = [_row_to_memory(r) for r in cur.fetchall()]
    except sqlite3.OperationalError:
        pass

    if not fts_rows:
        try:
            cur = conn.execute(
                "SELECT m.* FROM memory_fts f JOIN memories m ON m.id=f.id "
                "WHERE memory_fts MATCH ? LIMIT ?",
                (query, limit * 3),
            )
            fts_rows = [_row_to_memory(r) for r in cur.fetchall()]
            if project_id:
                fts_rows = [r for r in fts_rows if r.get("project_id") == project_id]
        except sqlite3.OperationalError:
            pass

    if fts_rows:
        return fts_rows

    return _like_fallback(conn, query, limit, project_id=project_id)


def count_memories(conn: sqlite3.Connection, project_id: str | None = None) -> int:
    if project_id:
        cur = conn.execute("SELECT COUNT(*) as c FROM memories WHERE project_id=?", (project_id,))
    else:
        cur = conn.execute("SELECT COUNT(*) as c FROM memories")
    return cur.fetchone()["c"]


# v0.5: Workflow tables methods


def create_session(
    conn: sqlite3.Connection,
    session_id: str,
    project_id: str,
    agent_id: str,
    basis_commit: str | None = None,
    basis_branch: str | None = None,
    automation_mode: str = "full",
    metadata: dict[str, Any] | None = None,
) -> str:
    ts = now_iso()
    conn.execute(
        "INSERT INTO sessions (id, project_id, agent_id, started_at, basis_commit, basis_branch, status, automation_mode, metadata_json) VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?)",
        (session_id, project_id, agent_id, ts, basis_commit, basis_branch, automation_mode, json.dumps(metadata or {}, ensure_ascii=False)),
    )
    return session_id


def get_session(conn: sqlite3.Connection, session_id: str, project_id: str) -> dict[str, Any] | None:
    cur = conn.execute("SELECT * FROM sessions WHERE id=? AND project_id=?", (session_id, project_id))
    row = cur.fetchone()
    if row is None:
        return None
    return {
        "id": row["id"],
        "project_id": row["project_id"],
        "agent_id": row["agent_id"],
        "started_at": row["started_at"],
        "ended_at": row["ended_at"],
        "basis_commit": row["basis_commit"],
        "basis_branch": row["basis_branch"],
        "status": row["status"],
        "last_event_at": row["last_event_at"],
        "automation_mode": row["automation_mode"],
        "metadata": json.loads(row["metadata_json"]) if row["metadata_json"] else {},
    }


def update_session_status(conn: sqlite3.Connection, session_id: str, project_id: str, status: str, last_event_at: str | None = None) -> None:
    if last_event_at:
        conn.execute("UPDATE sessions SET status=?, last_event_at=? WHERE id=? AND project_id=?", (status, last_event_at, session_id, project_id))
    else:
        conn.execute("UPDATE sessions SET status=? WHERE id=? AND project_id=?", (status, session_id, project_id))


def list_sessions(conn: sqlite3.Connection, project_id: str, status: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
    q = "SELECT * FROM sessions WHERE project_id=?"
    params: list[Any] = [project_id]
    if status:
        q += " AND status=?"
        params.append(status)
    q += " ORDER BY started_at DESC LIMIT ?"
    params.append(limit)
    cur = conn.execute(q, params)
    return [
        {
            "id": r["id"],
            "project_id": r["project_id"],
            "agent_id": r["agent_id"],
            "started_at": r["started_at"],
            "ended_at": r["ended_at"],
            "basis_commit": r["basis_commit"],
            "basis_branch": r["basis_branch"],
            "status": r["status"],
            "last_event_at": r["last_event_at"],
            "automation_mode": r["automation_mode"],
            "metadata": json.loads(r["metadata_json"]) if r["metadata_json"] else {},
        }
        for r in cur.fetchall()
    ]


def create_automation_run(
    conn: sqlite3.Connection,
    run_id: str,
    project_id: str,
    session_id: str,
    trigger: str,
    started_at: str,
    event_ids: list[str] | None = None,
    evidence_ids: list[str] | None = None,
    proposal_ids: list[str] | None = None,
    warnings: list[str] | None = None,
    error: str | None = None,
) -> str:
    conn.execute(
        "INSERT INTO automation_runs (id, project_id, session_id, trigger, status, started_at, created_event_ids, created_evidence_ids, created_proposal_ids, warnings, error) VALUES (?, ?, ?, ?, 'running', ?, ?, ?, ?, ?, ?)",
        (run_id, project_id, session_id, trigger, started_at, json.dumps(event_ids or [], ensure_ascii=False), json.dumps(evidence_ids or [], ensure_ascii=False), json.dumps(proposal_ids or [], ensure_ascii=False), json.dumps(warnings or [], ensure_ascii=False), error or ""),
    )
    return run_id


def update_automation_run(
    conn: sqlite3.Connection,
    run_id: str,
    project_id: str,
    status: str,
    finished_at: str | None = None,
    warnings: list[str] | None = None,
    error: str | None = None,
) -> None:
    if finished_at:
        conn.execute(
            "UPDATE automation_runs SET status=?, finished_at=?, warnings=?, error=? WHERE id=? AND project_id=?",
            (status, finished_at, json.dumps(warnings or [], ensure_ascii=False), error or "", run_id, project_id),
        )
    else:
        conn.execute(
            "UPDATE automation_runs SET status=?, warnings=?, error=? WHERE id=? AND project_id=?",
            (status, json.dumps(warnings or [], ensure_ascii=False), error or "", run_id, project_id),
        )


def list_automation_runs(conn: sqlite3.Connection, project_id: str, session_id: str | None = None, status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    q = "SELECT * FROM automation_runs WHERE project_id=?"
    params: list[Any] = [project_id]
    if session_id:
        q += " AND session_id=?"
        params.append(session_id)
    if status:
        q += " AND status=?"
        params.append(status)
    q += " ORDER BY started_at DESC LIMIT ?"
    params.append(limit)
    cur = conn.execute(q, params)
    return [
        {
            "id": r["id"],
            "project_id": r["project_id"],
            "session_id": r["session_id"],
            "trigger": r["trigger"],
            "status": r["status"],
            "started_at": r["started_at"],
            "finished_at": r["finished_at"],
            "created_event_ids": json.loads(r["created_event_ids"]) if r["created_event_ids"] else [],
            "created_evidence_ids": json.loads(r["created_evidence_ids"]) if r["created_evidence_ids"] else [],
            "created_proposal_ids": json.loads(r["created_proposal_ids"]) if r["created_proposal_ids"] else [],
            "warnings": json.loads(r["warnings"]) if r["warnings"] else [],
            "error": r["error"],
        }
        for r in cur.fetchall()
    ]


def create_handover_draft(
    conn: sqlite3.Connection,
    draft_id: str,
    project_id: str,
    session_id: str,
    task_id: str | None,
    report: dict[str, Any],
    source_event_ids: list[str],
    proposal_ids: list[str] | None = None,
    basis_commit: str | None = None,
    generated_by: str = "workflow",
) -> str:
    ts = now_iso()
    conn.execute(
        "INSERT INTO handover_drafts (id, project_id, session_id, task_id, status, report_json, source_event_ids, proposal_ids, basis_commit, generated_by, created_at, updated_at) VALUES (?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?, ?)",
        (draft_id, project_id, session_id, task_id, json.dumps(report, ensure_ascii=False), json.dumps(source_event_ids, ensure_ascii=False), json.dumps(proposal_ids or [], ensure_ascii=False), basis_commit, generated_by, ts, ts),
    )
    return draft_id


def get_handover_draft(conn: sqlite3.Connection, draft_id: str, project_id: str) -> dict[str, Any] | None:
    cur = conn.execute("SELECT * FROM handover_drafts WHERE id=? AND project_id=?", (draft_id, project_id))
    row = cur.fetchone()
    if row is None:
        return None
    return {
        "id": row["id"],
        "project_id": row["project_id"],
        "session_id": row["session_id"],
        "task_id": row["task_id"],
        "status": row["status"],
        "report": json.loads(row["report_json"]),
        "source_event_ids": json.loads(row["source_event_ids"]),
        "proposal_ids": json.loads(row["proposal_ids"]) if row["proposal_ids"] else [],
        "basis_commit": row["basis_commit"],
        "generated_by": row["generated_by"],
        "applied_handover_id": row["applied_handover_id"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def update_handover_draft_status(conn: sqlite3.Connection, draft_id: str, project_id: str, status: str, applied_handover_id: str | None = None) -> None:
    if applied_handover_id:
        conn.execute("UPDATE handover_drafts SET status=?, applied_handover_id=? WHERE id=? AND project_id=?", (status, applied_handover_id, draft_id, project_id))
    else:
        conn.execute("UPDATE handover_drafts SET status=? WHERE id=? AND project_id=?", (status, draft_id, project_id))


def list_handover_drafts(conn: sqlite3.Connection, project_id: str, session_id: str | None = None, status: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
    q = "SELECT * FROM handover_drafts WHERE project_id=?"
    params: list[Any] = [project_id]
    if session_id:
        q += " AND session_id=?"
        params.append(session_id)
    if status:
        q += " AND status=?"
        params.append(status)
    q += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    cur = conn.execute(q, params)
    return [
        {
            "id": r["id"],
            "project_id": r["project_id"],
            "session_id": r["session_id"],
            "task_id": r["task_id"],
            "status": r["status"],
            "report": json.loads(r["report_json"]),
            "source_event_ids": json.loads(r["source_event_ids"]),
            "proposal_ids": json.loads(r["proposal_ids"]) if r["proposal_ids"] else [],
            "basis_commit": r["basis_commit"],
            "generated_by": r["generated_by"],
            "applied_handover_id": r["applied_handover_id"],
            "created_at": r["created_at"],
            "updated_at": r["updated_at"],
        }
        for r in cur.fetchall()
    ]
