from __future__ import annotations

import sqlite3
from typing import Any, Protocol

from . import repository as repo
from .models import content_to_text

STATUS_RANK = {"verified": 4, "active": 4, "proposed": 2, "observed": 2, "draft": 1, "deprecated": 0, "invalid": 0}
RELEVANCE_THRESHOLD = 0.34
MIN_MATCHED_TERMS_RATIO = 0.25


class SearchProvider(Protocol):
    def search(self, conn: sqlite3.Connection, project_id: str, query: str, scope: list[str] | None, limit: int) -> list[dict[str, Any]]: ...


def _matched_terms(query: str, text: str) -> list[str]:
    raw = query.replace("？", " ").replace("?", " ").replace("，", " ").replace(",", " ").strip()
    toks = [t for t in raw.split() if t.strip()]
    if not toks:
        toks = [raw.strip()]
    terms: list[str] = []
    low_text = text.lower()
    for t in toks:
        if t.lower() in low_text:
            terms.append(t)
        elif len(t) > 4 and not t.isascii():
            for i in range(len(t) - 1):
                bg = t[i : i + 2]
                if bg.lower() in low_text and bg not in terms:
                    terms.append(bg)
            if t not in terms and any(bg in terms for bg in [t[i : i + 2] for i in range(len(t) - 1)]):
                terms.append(t)
    return terms


def _score_row(row: dict[str, Any], query: str, matched_terms: list[str], has_evidence: bool, scope: list[str] | None) -> float:
    text = content_to_text(row["content"]) + " " + " ".join(row.get("tags") or [])
    if not text.strip():
        return 0.0
    q_terms = [t for t in query.replace("？", " ").replace("?", " ").split() if t.strip()] or [query.strip()]
    hit_ratio = len(matched_terms) / max(len(q_terms), 1)
    if hit_ratio < MIN_MATCHED_TERMS_RATIO and not matched_terms:
        return 0.0
    status_w = STATUS_RANK.get(row.get("status", "draft"), 1) / 4.0
    ev_bonus = 0.15 if has_evidence else 0
    scope_bonus = 0.0
    if scope:
        content_scope = row.get("content", {}).get("scope") if isinstance(row.get("content"), dict) else None
        tags = row.get("tags") or []
        if content_scope in scope or any(s in tags for s in scope) or row.get("type") in scope:
            scope_bonus = 0.2
    score = hit_ratio * 0.55 + status_w * 0.2 + ev_bonus + scope_bonus
    if matched_terms:
        score += min(len(matched_terms) * 0.04, 0.12)
    return round(score, 4)


def _has_evidence(conn: sqlite3.Connection, mem_id: str, project_id: str) -> bool:
    links = repo.get_links(conn, from_id=mem_id, project_id=project_id)
    return any(lk["relation"] == "evidence_of" for lk in links)


class FTSProvider:
    def search(self, conn: sqlite3.Connection, project_id: str, query: str, scope: list[str] | None, limit: int) -> list[dict[str, Any]]:
        raw_candidates = repo.fts_search(conn, query, limit=limit * 4, project_id=project_id)
        scored: list[tuple[float, str, list[str], dict[str, Any]]] = []
        mode = "fts"
        if not raw_candidates:
            mode = "none"
        for r in raw_candidates:
            text = content_to_text(r["content"]) + " " + " ".join(r.get("tags") or [])
            terms = _matched_terms(query, text)
            ev = _has_evidence(conn, r["id"], project_id)
            score = _score_row(r, query, terms, ev, scope)
            if scope:
                tags = r.get("tags") or []
                content_scope = r.get("content", {}).get("scope") if isinstance(r.get("content"), dict) else None
                if scope and r.get("type") not in scope and content_scope not in scope and not any(s in tags for s in scope):
                    continue
            scored.append((score, mode if terms else "like_fallback", terms, r))
        scored.sort(key=lambda x: (x[0], x[3].get("updated_at", "")), reverse=True)
        out: list[dict[str, Any]] = []
        for score, m, terms, r in scored:
            if score < RELEVANCE_THRESHOLD:
                continue
            out.append({"score": score, "match_mode": m, "matched_terms": terms, "row": r})
            if len(out) >= limit:
                break
        return out


DEFAULT_PROVIDER = FTSProvider()


def ranked_search(
    conn: sqlite3.Connection,
    query: str,
    scope: list[str] | None = None,
    limit: int = 8,
    project_id: str | None = None,
    provider: SearchProvider | None = None,
) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
    p = provider or DEFAULT_PROVIDER
    pid = project_id or "default"
    scored = p.search(conn, pid, query, scope, limit)
    if not scored:
        fts_any = repo.fts_search(conn, query, limit=1, project_id=pid if project_id else None)
        mode = "none" if not fts_any else "like_fallback"
        return mode, [], []
    mode = scored[0]["match_mode"] if scored else "fts"
    if all(s["match_mode"] == "like_fallback" for s in scored):
        mode = "like_fallback"
    elif any(s["match_mode"] == "fts" for s in scored):
        mode = "fts"
    rows = [s["row"] for s in scored]
    matches = [{"id": s["row"]["id"], "score": s["score"], "matched_terms": s["matched_terms"], "match_mode": s["match_mode"]} for s in scored]
    return mode, rows, matches


def compute_confidence(facts: list[dict[str, Any]], evidence_count: int) -> float | None:
    if not facts:
        return 0.0
    verified = sum(1 for f in facts if f["status"] in ("verified", "active"))
    ratio = verified / len(facts) if facts else 0
    ev_bonus = min(evidence_count * 0.1, 0.3)
    base = 0.5 + ratio * 0.4 + ev_bonus
    return round(min(base, 0.95), 2)
