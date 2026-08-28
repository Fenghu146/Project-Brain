from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from . import repository as repo
from .models import now_iso

CURATOR_VERSION = "rule-v1"


def _is_stale_memory(mem: dict[str, Any], conn: sqlite3.Connection) -> tuple[bool, str | None]:
    from datetime import datetime, timezone

    valid_until = mem.get("valid_until")
    if valid_until:
        try:
            dt = datetime.fromisoformat(valid_until.replace("Z", "+00:00"))
            if dt < datetime.now(timezone.utc):
                return True, "valid_until 已过期"
        except Exception:
            pass
    due = mem.get("verification_due_at")
    if due:
        try:
            dt = datetime.fromisoformat(due.replace("Z", "+00:00"))
            if dt < datetime.now(timezone.utc):
                return True, "verification_due_at 已过期"
        except Exception:
            pass
    links = repo.get_links(conn, from_id=mem["id"], project_id=mem.get("project_id") or "default")
    for lk in links:
        if lk["relation"] == "evidence_of":
            ev = repo.get_evidence(conn, lk["to_id"], project_id=mem.get("project_id"))
            if ev:
                src = ev.get("source") or ""
                if src and "/" in src and not src.startswith("http"):
                    from pathlib import Path as P

                    if not P(src).exists() and not P("/Users/fenghui/Desktop/Project Brain").joinpath(src).exists():
                        return True, f"证据失效：{src}"
    return False, None


def build_model(conn: sqlite3.Connection, project_id: str) -> tuple[dict[str, Any], list[str], float | None]:
    identities = repo.list_memories(conn, project_id=project_id, mem_type="identity", limit=1)
    states = repo.list_memories(conn, project_id=project_id, mem_type="state", limit=1)
    tasks_all = repo.list_memories(conn, project_id=project_id, mem_type="task", limit=50)
    decisions = repo.list_memories(conn, project_id=project_id, mem_type="decision", limit=50)
    knowledges = repo.list_memories(conn, project_id=project_id, mem_type="knowledge", limit=50)
    experiences = repo.list_memories(conn, project_id=project_id, mem_type="experience", limit=50)
    handovers = repo.list_handovers(conn, project_id=project_id, limit=5)
    snapshots = repo.list_snapshots(conn, project_id=project_id, limit=1)

    now_valid = []
    stale: list[dict[str, Any]] = []
    for lst in [decisions, knowledges, experiences]:
        for m in lst:
            is_stale, why = _is_stale_memory(m, conn)
            if is_stale:
                stale.append({"id": m["id"], "reason": why})

    active_decisions = [d for d in decisions if d["status"] in ("verified", "active") and not any(s["id"] == d["id"] for s in stale)]
    risks = [e for e in experiences if isinstance(e["content"], dict) and e["content"].get("result") == "failed"]
    work_active = [t for t in tasks_all if t.get("task_status") in ("in_progress", "blocked")]

    source_ids: list[str] = []
    for lst in [identities, states, active_decisions[:5], risks[:5], work_active[:5], handovers[:1]]:
        for m in lst:
            mid = m.get("id")
            if mid and mid not in source_ids:
                source_ids.append(mid)

    model: dict[str, Any] = {
        "identity": identities[0]["content"] if identities else None,
        "current_state": states[0]["content"] if states else None,
        "architecture": [k["content"] for k in knowledges if isinstance(k["content"], dict) and k["content"].get("scope") == "architecture"][:5],
        "decisions": [{"id": d["id"], "decision": d["content"].get("decision") if isinstance(d["content"], dict) else str(d["content"])} for d in active_decisions[:5]],
        "risks": [{"id": r["id"], "lesson": r["content"].get("lesson") if isinstance(r["content"], dict) else str(r["content"])} for r in risks[:5]],
        "work": {"active": [{"id": t["id"], "title": t["content"].get("title") if isinstance(t["content"], dict) else str(t["content"])} for t in work_active[:5]], "handovers": [{"id": h["id"], "status": h["status"]} for h in handovers[:3]]},
        "provenance": {mid: [lk["to_id"] for lk in repo.get_links(conn, from_id=mid, project_id=project_id)] for mid in source_ids},
        "stale": stale,
    }
    confs = [m.get("confidence") for m in identities + states + active_decisions if m.get("confidence") is not None]
    confidence = sum(confs) / len(confs) if confs else None
    return model, source_ids, confidence


def create_snapshot(conn: sqlite3.Connection, project_id: str, basis_commit: str | None = None, basis_branch: str | None = None) -> str:
    if not basis_commit:
        try:
            import subprocess

            basis_commit = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True, timeout=5).strip()
            basis_branch = subprocess.check_output(["git", "rev-parse", "--abbrev-ref", "HEAD"], text=True, timeout=5).strip()
        except Exception:
            basis_commit = None
            basis_branch = None
    model, source_ids, confidence = build_model(conn, project_id)
    sid = repo.create_snapshot(conn, project_id=project_id, model_json=model, source_ids=source_ids, basis_commit=basis_commit, basis_branch=basis_branch, confidence=confidence, curator_version=CURATOR_VERSION)
    return sid


def rebuild_snapshot(conn: sqlite3.Connection, snapshot_id: str, project_id: str | None = None) -> dict[str, Any]:
    # snapshot was deleted; recover its project_id/basis from caller-provided data or scan
    snap = repo.get_snapshot(conn, snapshot_id, project_id=project_id)
    if snap is None and project_id:
        cur = conn.execute("SELECT * FROM model_snapshots WHERE id=?", (snapshot_id,))
        row = cur.fetchone()
        if row:
            snap = repo.get_snapshot(conn, snapshot_id)
    if snap is None:
        # caller deleted it; rebuild from current model with same id
        pid = project_id or "default"
        model, source_ids, confidence = build_model(conn, pid)
        new_id = repo.create_snapshot(conn, project_id=pid, model_json=model, source_ids=source_ids, basis_commit=None, basis_branch=None, confidence=confidence, curator_version=CURATOR_VERSION, snapshot_id=snapshot_id)
        return repo.get_snapshot(conn, new_id, project_id=pid)  # type: ignore[return-value]
    pid = snap["project_id"]
    basis_commit = snap["basis_commit"]
    basis_branch = snap["basis_branch"]
    conn.execute("DELETE FROM model_snapshots WHERE id=?", (snapshot_id,))
    model, source_ids, confidence = build_model(conn, pid)
    new_id = repo.create_snapshot(conn, project_id=pid, model_json=model, source_ids=source_ids, basis_commit=basis_commit, basis_branch=basis_branch, confidence=confidence, curator_version=CURATOR_VERSION, snapshot_id=snapshot_id)
    return repo.get_snapshot(conn, new_id, project_id=pid)  # type: ignore[return-value]
