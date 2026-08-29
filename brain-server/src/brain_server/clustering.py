from __future__ import annotations

import re
import string
from typing import Any

from .curator import jaccard
from .models import content_to_text


def _normalize(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[{}]".format(re.escape(string.punctuation)), " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


_TYPE_PRIORITY: dict[str, int] = {
    "decision": 4,
    "knowledge": 3,
    "state": 2,
    "task": 1,
    "experience": 1,
    "identity": 1,
    "evidence": 0,
}

_STATUS_PRIORITY: dict[str, int] = {
    "active": 3,
    "verified": 3,
    "proposed": 1,
    "draft": 0,
    "observed": 1,
    "deprecated": -1,
    "invalid": -1,
}


def _candidate_text(c: dict[str, Any]) -> str:
    row = c.get("row", c)
    content = row.get("content", {})
    base = content_to_text(content)
    tags = " ".join(row.get("tags") or [])
    return _normalize(f"{base} {tags}")


def _decision_value(c: dict[str, Any]) -> str | None:
    row = c.get("row", c)
    content = row.get("content")
    if isinstance(content, dict) and content.get("decision"):
        return _normalize(str(content["decision"]))
    return None


def _pick_primary(members: list[dict[str, Any]]) -> dict[str, Any]:
    def _key(c: dict[str, Any]) -> tuple[int, int, str]:
        row = c.get("row", c)
        tp = _TYPE_PRIORITY.get(row.get("type", ""), 0)
        sp = _STATUS_PRIORITY.get(row.get("status", ""), 0)
        return (tp, sp, row.get("updated_at", ""))

    return max(members, key=_key)


def cluster_candidates(
    candidates: list[dict[str, Any]],
    jaccard_threshold: float = 0.55,
) -> list[dict[str, Any]]:
    if not candidates:
        return []

    n = len(candidates)
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    texts = [_candidate_text(c) for c in candidates]
    decisions = [_decision_value(c) for c in candidates]

    for i in range(n):
        for j in range(i + 1, n):
            if decisions[i] and decisions[j] and decisions[i] == decisions[j]:
                union(i, j)
                continue
            if jaccard(texts[i], texts[j]) >= jaccard_threshold:
                union(i, j)

    clusters: dict[int, list[int]] = {}
    for idx in range(n):
        root = find(idx)
        clusters.setdefault(root, []).append(idx)

    result: list[dict[str, Any]] = []
    for cid, indices in enumerate(clusters.values()):
        members = [candidates[i] for i in indices]
        primary = _pick_primary(members)
        primary_id = (primary.get("row", primary).get("id", ""))

        supporting_ids: list[str] = []
        supporting_evidence_ids: list[str] = []
        reasons: list[str] = []

        for m in members:
            if m is primary:
                continue
            row = m.get("row", m)
            mid = row.get("id", "")
            if mid:
                supporting_ids.append(mid)
            reasons.append(f"jaccard cluster {cid}")

        # Deduplicate reasons
        merged_reason = "; ".join(sorted(set(reasons))) if reasons else "single"

        result.append({
            "cluster_id": f"C-{cid:03d}",
            "primary": primary,
            "primary_id": primary_id,
            "supporting_ids": supporting_ids,
            "supporting_evidence_ids": supporting_evidence_ids,
            "merged_reason": merged_reason,
            "size": len(members),
        })

    # Sort clusters by primary score descending
    result.sort(key=lambda c: c["primary"].get("score", 0), reverse=True)
    return result
