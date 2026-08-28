from __future__ import annotations

import json
from typing import Any

from .models import VALID_MEMORY_STATUSES, VALID_TASK_STATUSES


def classify_record(content: Any) -> str:
    if isinstance(content, str):
        return "knowledge"
    if not isinstance(content, dict):
        return "knowledge"
    if "decision" in content and "reason" in content:
        return "decision"
    if "attempt" in content and "result" in content:
        return "experience"
    if "title" in content and ("remaining" in content or "next_step" in content):
        return "task"
    return "knowledge"


def jaccard(a: str, b: str) -> float:
    sa = set(a.lower().split())
    sb = set(b.lower().split())
    if not sa and not sb:
        return 1.0
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def deduplicate_check(new_text: str, existing_texts: list[str], threshold: float = 0.8) -> bool:
    for t in existing_texts:
        if jaccard(new_text, t) >= threshold:
            return True
    return False


def validate_status(mem_type: str, status: str | None, task_status: str | None, has_evidence: bool) -> tuple[str, str | None, list[str]]:
    warnings: list[str] = []
    ts: str | None = task_status
    if mem_type == "task":
        if ts and ts not in VALID_TASK_STATUSES:
            warnings.append(f"invalid task_status {ts}, fallback to in_progress")
            ts = "in_progress"
        if not ts:
            cands = (status or "").strip()
            if cands in VALID_TASK_STATUSES:
                ts = cands
            else:
                ts = "in_progress"
        s = status or "active"
        if s not in VALID_MEMORY_STATUSES:
            s = "active"
        return s, ts, warnings
    s = status or "draft"
    if s not in VALID_MEMORY_STATUSES:
        warnings.append(f"invalid status {s}, fallback to draft")
        s = "draft"
    if mem_type == "decision" and not has_evidence and s in ("verified", "active"):
        warnings.append(f"Decision without evidence downgraded from {s} to proposed")
        s = "proposed"
    if mem_type == "decision" and not has_evidence and s == "draft":
        s = "proposed"
        warnings.append("Decision without evidence set to proposed")
    return s, None, warnings


def type_required_fields(mem_type: str, content: dict[str, Any] | str) -> list[str]:
    if isinstance(content, str):
        return []
    req: dict[str, list[str]] = {
        "decision": ["decision", "reason"],
        "experience": ["attempt", "result"],
        "task": ["title"],
        "knowledge": [],
        "identity": ["name"],
        "state": ["current_goal"],
    }
    fields = req.get(mem_type, [])
    return [f for f in fields if f not in content or not content[f]]


def needs_verification(mem_type: str, status: str, has_evidence: bool) -> bool:
    if status in ("draft", "proposed", "observed"):
        return True
    if mem_type in ("decision", "experience") and not has_evidence:
        return True
    return False
