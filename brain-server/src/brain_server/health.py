from __future__ import annotations

import sqlite3
from datetime import datetime, timezone, timedelta
from typing import Any

from . import repository as repo
from .curator import jaccard
from .models import content_to_text


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def memory_health(conn: sqlite3.Connection, project_id: str) -> dict[str, Any]:
    warnings: list[dict[str, Any]] = []
    memories = repo.list_memories(conn, project_id=project_id, limit=1000)

    # 1. Orphan decisions (no evidence_of link)
    for m in memories:
        if m["type"] == "decision" and m["status"] in ("proposed", "active", "verified"):
            links = repo.get_links(conn, from_id=m["id"], project_id=project_id)
            has_ev = any(lk["relation"] == "evidence_of" for lk in links)
            if not has_ev:
                warnings.append({"kind": "orphan_decision", "id": m["id"], "detail": "decision without evidence_of link"})

    # 2. Duplicate knowledge (Jaccard >= 0.8)
    for i in range(len(memories)):
        for j in range(i + 1, len(memories)):
            a, b = memories[i], memories[j]
            if a["type"] != b["type"]:
                continue
            ta = content_to_text(a["content"])
            tb = content_to_text(b["content"])
            if jaccard(ta, tb) >= 0.8:
                warnings.append({"kind": "duplicate", "ids": [a["id"], b["id"]], "detail": f"jaccard >= 0.8 ({a['type']})"})
                break

    # 3. Stale but referenced
    now = datetime.now(timezone.utc)
    for m in memories:
        vu = _parse_dt(m.get("valid_until"))
        if vu and vu < now:
            back_links = repo.get_links(conn, to_id=m["id"], project_id=project_id)
            if len(back_links) > 2:
                warnings.append({"kind": "stale_referenced", "id": m["id"], "detail": f"stale but referenced {len(back_links)} times"})

    # 4. Conflicting active memories
    for m in memories:
        if m["status"] in ("active", "verified"):
            links = repo.get_links(conn, from_id=m["id"], project_id=project_id)
            for lk in links:
                if lk["relation"] == "conflicts_with":
                    other = repo.get_memory(conn, lk["to_id"], project_id=project_id)
                    if other and other["status"] in ("active", "verified"):
                        warnings.append({"kind": "conflict", "ids": [m["id"], other["id"]], "detail": "both active/verified but conflicts_with"})

    # 5. Long-unupdated
    stale_threshold = now - timedelta(days=90)
    for m in memories:
        ua = _parse_dt(m.get("updated_at"))
        if ua and ua < stale_threshold:
            warnings.append({"kind": "long_unupdated", "id": m["id"], "detail": f"not updated since {m['updated_at']}"})

    # Deduplicate warnings by kind+id
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for w in warnings:
        key = w.get("id", str(w.get("ids", w["kind"]))) + w["kind"]
        if key not in seen:
            seen.add(key)
            deduped.append(w)

    return {"warnings": deduped, "total_memories": len(memories)}


def brain_health(conn: sqlite3.Connection, project_id: str) -> dict[str, Any]:
    ev_health = repo.check_evidence_health(conn, project_id)
    reachable = sum(1 for h in ev_health if h["health"] == "reachable")
    mem_health = memory_health(conn, project_id)

    # Provenance coverage
    memories = repo.list_memories(conn, project_id=project_id, limit=1000)
    with_links = 0
    for m in memories:
        links = repo.get_links(conn, from_id=m["id"], project_id=project_id)
        if links:
            with_links += 1
    provenance_coverage = round(with_links / max(len(memories), 1), 2)

    # Workflow health
    try:
        from .repository import list_sessions  # type: ignore[attr-defined]

        sessions = list_sessions(conn, project_id=project_id, status="active", limit=20)
        active_sessions = len(sessions)
    except Exception:
        active_sessions = 0
        try:
            cur = conn.execute("SELECT count(*) as c FROM sessions WHERE project_id=? AND status='active'", (project_id,))
            active_sessions = cur.fetchone()["c"]
        except Exception:
            pass

    return {
        "evidence": {"total": len(ev_health), "reachable": reachable},
        "memory": mem_health,
        "provenance_coverage": provenance_coverage,
        "workflow": {"active_sessions": active_sessions},
    }
