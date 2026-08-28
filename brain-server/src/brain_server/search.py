from __future__ import annotations

import sqlite3
from typing import Any

from .repository import fts_search

STATUS_RANK = {"verified": 4, "active": 4, "proposed": 2, "observed": 2, "draft": 1, "deprecated": 0, "invalid": 0}


def ranked_search(
    conn: sqlite3.Connection,
    query: str,
    scope: list[str] | None = None,
    limit: int = 8,
) -> list[dict[str, Any]]:
    results = fts_search(conn, query, limit=limit * 3)
    if scope:
        scope_set = set(scope)
        results = [r for r in results if r["type"] in scope_set or any(s in (r.get("tags") or []) for s in scope_set)]
    results.sort(key=lambda r: (STATUS_RANK.get(r["status"], 0), r["updated_at"]), reverse=True)
    return results[:limit]


def compute_confidence(facts: list[dict[str, Any]], evidence_count: int) -> float | None:
    if not facts:
        return None
    verified = sum(1 for f in facts if f["status"] in ("verified", "active"))
    ratio = verified / len(facts) if facts else 0
    ev_bonus = min(evidence_count * 0.1, 0.3)
    base = 0.5 + ratio * 0.4 + ev_bonus
    return round(min(base, 0.95), 2)
