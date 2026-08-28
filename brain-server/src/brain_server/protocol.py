from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from . import repository as repo
from .curator import deduplicate_check, needs_verification, validate_status
from .db import get_connection, init_db
from .models import (
    BrainAskRequest,
    BrainAskResponse,
    BrainHandoverRequest,
    BrainHandoverResponse,
    BrainOnboardRequest,
    BrainOnboardResponse,
    BrainRecordRequest,
    BrainRecordResponse,
    content_to_text,
    now_iso,
)
from .search import compute_confidence, ranked_search


def ensure_db(db_path: str | Path | None = None) -> sqlite3.Connection:
    conn = get_connection(db_path)
    init_db(db_path)
    return conn


def brain_record(req: BrainRecordRequest, db_path: str | Path | None = None) -> BrainRecordResponse:
    conn = ensure_db(db_path)
    accepted: list[str] = []
    deduplicated: list[str] = []
    needs_verif: list[str] = []
    warnings: list[str] = []
    state_updates: list[str] = []

    existing = repo.list_memories(conn, limit=500)
    existing_texts = [content_to_text(m["content"]) for m in existing]

    try:
        for rec in req.records:
            mem_type = rec.type
            if mem_type in ("event", "evidence"):
                if mem_type == "evidence":
                    content = rec.content if isinstance(rec.content, dict) else {"text": rec.content}
                    ev_id = repo.create_evidence(
                        conn,
                        ev_type=content.get("type", "observed"),
                        source=content.get("source", "unknown"),
                        description=content.get("description"),
                        metadata=content.get("metadata"),
                        status=rec.status or "observed",
                    )
                    accepted.append(ev_id)
                    repo.create_event(conn, action="record", agent_id=req.agent_id, session_id=req.session_id, summary=f"record evidence {ev_id}", payload={"evidence_id": ev_id})
                elif mem_type == "event":
                    content = rec.content if isinstance(rec.content, dict) else {"summary": rec.content}
                    ev_id = repo.create_event(
                        conn,
                        action=content.get("action", "record"),
                        agent_id=req.agent_id,
                        session_id=req.session_id,
                        target=content.get("target"),
                        summary=content.get("summary", content_to_text(content)),
                        payload=content,
                    )
                    accepted.append(ev_id)
                continue

            if mem_type not in ("identity", "state", "knowledge", "experience", "decision", "task"):
                warnings.append(f"unknown type {mem_type}, treated as knowledge")
                mem_type = "knowledge"

            content = rec.content
            text = content_to_text(content)
            has_evidence = bool(rec.evidence)

            if deduplicate_check(text, existing_texts):
                deduplicated.append(text[:80])
                warnings.append(f"possible duplicate for {mem_type}: {text[:60]}")

            status, ws = validate_status(mem_type, rec.status, has_evidence)
            warnings.extend(ws)

            mem_id = repo.create_memory(
                conn,
                mem_type=mem_type,
                content=content,
                status=status,
                confidence=rec.confidence,
                tags=rec.tags,
                created_by=req.agent_id,
            )
            accepted.append(mem_id)
            existing_texts.append(text)

            if rec.evidence:
                for ev in rec.evidence:
                    ev_id = repo.create_evidence(
                        conn,
                        ev_type=ev.get("type", "observed"),
                        source=ev.get("source", "unknown"),
                        description=ev.get("description"),
                        metadata=ev.get("metadata"),
                        status="observed",
                    )
                    repo.create_link(conn, mem_id, "evidence_of", ev_id)
                    accepted.append(ev_id)

            if rec.tags:
                for tag in rec.tags:
                    pass

            if needs_verification(mem_type, status, has_evidence):
                needs_verif.append(mem_id)

            if mem_type == "state":
                state_updates.append(mem_id)

            repo.create_event(conn, action="record", agent_id=req.agent_id, session_id=req.session_id, summary=f"record {mem_type} {mem_id}", payload={"memory_id": mem_id, "type": mem_type})

        conn.commit()
    finally:
        conn.close()

    return BrainRecordResponse(
        accepted=accepted,
        deduplicated=deduplicated,
        needs_verification=needs_verif,
        warnings=warnings,
        state_updates=state_updates,
    )


def brain_ask(req: BrainAskRequest, db_path: str | Path | None = None) -> BrainAskResponse:
    conn = ensure_db(db_path)
    try:
        results = ranked_search(conn, req.question, scope=req.scope, limit=req.limit)
        facts = [{"id": r["id"], "type": r["type"], "status": r["status"]} for r in results]

        evidence_list: list[dict[str, Any]] = []
        if req.include_evidence:
            for r in results:
                links = repo.get_links(conn, from_id=r["id"])
                for lk in links:
                    if lk["relation"] == "evidence_of":
                        ev = repo.get_evidence(conn, lk["to_id"])
                        if ev:
                            evidence_list.append(ev)
            if not evidence_list:
                for ev in repo.list_evidence(conn, limit=5):
                    if any(ev["source"] in content_to_text(r["content"]) for r in results):
                        evidence_list.append(ev)

        if results:
            answer_parts = []
            for r in results[:3]:
                c = r["content"]
                if isinstance(c, dict):
                    answer_parts.append(c.get("decision") or c.get("lesson") or c.get("content") or c.get("text") or json.dumps(c, ensure_ascii=False)[:200])
                else:
                    answer_parts.append(str(c)[:200])
            answer = "；".join(answer_parts)
        else:
            answer = "未找到相关记录。"

        uncertainties: list[str] = []
        has_unverified = any(r["status"] in ("draft", "proposed", "observed") for r in results)
        if has_unverified:
            uncertainties.append("部分结果尚未验证，需进一步确认。")
        if not evidence_list and results:
            uncertainties.append("相关记录缺少可验证证据。")
        if not results:
            uncertainties.append("无匹配记录，建议检查关键词或补充记录。")

        confidence = compute_confidence(results, len(evidence_list))
        return BrainAskResponse(answer=answer, facts=facts, evidence=evidence_list, uncertainties=uncertainties, confidence=confidence)
    finally:
        conn.close()


def brain_onboard(req: BrainOnboardRequest, db_path: str | Path | None = None) -> BrainOnboardResponse:
    conn = ensure_db(db_path)
    try:
        identities = repo.list_memories(conn, mem_type="identity", limit=1)
        states = repo.list_memories(conn, mem_type="state", limit=1)
        tasks = repo.list_memories(conn, mem_type="task", limit=5)
        decisions = repo.list_memories(conn, mem_type="decision", limit=5)
        experiences = repo.list_memories(conn, mem_type="experience", limit=5)

        active_tasks = [t for t in tasks if t["status"] in ("active", "proposed", "draft")]
        important_decisions = [d for d in decisions if d["status"] in ("active", "verified")]
        if not important_decisions:
            important_decisions = decisions[:2]
        known_failures = [e for e in experiences if isinstance(e["content"], dict) and e["content"].get("result") == "failed"]
        if not known_failures:
            known_failures = [e for e in experiences if e["status"] in ("verified", "active")][:2]

        brief: dict[str, Any] = {}
        if identities:
            c = identities[0]["content"]
            brief["identity"] = c.get("text") or c.get("purpose") or c.get("name") or json.dumps(c, ensure_ascii=False)[:300]
        else:
            brief["identity"] = "未设置项目身份。"

        if states:
            c = states[0]["content"]
            if isinstance(c, dict):
                brief["current_state"] = c.get("current_goal") or c.get("text") or json.dumps(c, ensure_ascii=False)[:300]
                brief["open_questions"] = c.get("open_questions", [])
                brief["blockers"] = c.get("blockers", [])
            else:
                brief["current_state"] = str(c)[:300]
        else:
            brief["current_state"] = "未设置项目状态。"

        brief["active_tasks"] = [{"id": t["id"], "title": (t["content"].get("title") if isinstance(t["content"], dict) else str(t["content"])[:80]), "status": t["status"]} for t in active_tasks[:3]]
        brief["important_decisions"] = [{"id": d["id"], "summary": (d["content"].get("decision") if isinstance(d["content"], dict) else str(d["content"])[:80])} for d in important_decisions[:3]]
        brief["known_failures"] = [{"id": e["id"], "summary": (e["content"].get("lesson") or e["content"].get("attempt") if isinstance(e["content"], dict) else str(e["content"])[:80])} for e in known_failures[:3]]

        if states and isinstance(states[0]["content"], dict):
            brief["recommended_next_step"] = states[0]["content"].get("recommended_next_step") or (tasks[0]["content"].get("next_step") if tasks and isinstance(tasks[0]["content"], dict) else None) or "暂无明确下一步。"
        else:
            brief["recommended_next_step"] = "暂无明确下一步。"

        if req.focus:
            focused = ranked_search(conn, req.focus, limit=5)
            brief["focus_matches"] = [{"id": r["id"], "type": r["type"], "status": r["status"]} for r in focused]

        source_ids = []
        for lst in [identities, states, active_tasks, important_decisions, known_failures]:
            for m in lst[:3]:
                if m["id"] not in source_ids:
                    source_ids.append(m["id"])

        all_facts = identities + states + tasks + decisions + experiences
        ev_count = len(repo.list_evidence(conn, limit=100))
        confidence = compute_confidence(all_facts, ev_count)

        if req.token_budget:
            brief = _truncate_brief(brief, req.token_budget)

        return BrainOnboardResponse(project_id=req.project_id, generated_at=now_iso(), brief=brief, source_ids=source_ids, confidence=confidence)
    finally:
        conn.close()


def _truncate_brief(brief: dict[str, Any], token_budget: int) -> dict[str, Any]:
    char_budget = token_budget * 4
    text = json.dumps(brief, ensure_ascii=False)
    if len(text) <= char_budget:
        return brief
    brief = dict(brief)
    if "focus_matches" in brief:
        del brief["focus_matches"]
        text = json.dumps(brief, ensure_ascii=False)
        if len(text) <= char_budget:
            return brief
    for k in ["known_failures", "important_decisions", "active_tasks"]:
        if k in brief and isinstance(brief[k], list) and len(brief[k]) > 1:
            brief[k] = brief[k][:1]
            text = json.dumps(brief, ensure_ascii=False)
            if len(text) <= char_budget:
                return brief
    return brief


def brain_handover(req: BrainHandoverRequest, db_path: str | Path | None = None) -> BrainHandoverResponse:
    conn = ensure_db(db_path)
    db_file = conn.execute("PRAGMA database_list").fetchone()
    try:
        report: dict[str, Any] = {
            "task_id": req.task_id,
            "status": req.status,
            "completed": req.completed,
            "failed": req.failed,
            "discovered": req.discovered,
            "remaining": req.remaining,
            "next_step": req.recommended_next_step,
            "evidence_ids": req.evidence_ids,
            "agent_id": req.agent_id,
            "session_id": req.session_id,
        }
        handover_id = repo.create_handover(conn, task_id=req.task_id, agent_id=req.agent_id, session_id=req.session_id, status=req.status, report=report)
        brain_updates: list[str] = [handover_id]

        if req.task_id:
            task = repo.get_memory(conn, req.task_id)
            if task and isinstance(task["content"], dict):
                content = dict(task["content"])
                if req.completed:
                    content["completed"] = content.get("completed", []) + req.completed
                if req.remaining:
                    content["remaining"] = req.remaining
                if req.recommended_next_step:
                    content["next_step"] = req.recommended_next_step
                new_status = "completed" if req.status == "completed" else "in_progress"
                repo.update_memory(conn, req.task_id, content=content, status=new_status)
                brain_updates.append(req.task_id)

        states = repo.list_memories(conn, mem_type="state", limit=1)
        if states:
            s = states[0]
            c = dict(s["content"]) if isinstance(s["content"], dict) else {"text": str(s["content"])}
            c["recent_changes"] = (c.get("recent_changes", []) + req.completed)[:10]
            if req.remaining:
                c["open_questions"] = req.remaining[:5]
            if req.recommended_next_step:
                c["recommended_next_step"] = req.recommended_next_step
            repo.update_memory(conn, s["id"], content=c)
            brain_updates.append(s["id"])

        repo.create_event(conn, action="handover", agent_id=req.agent_id, session_id=req.session_id, target=req.task_id, summary=f"handover {handover_id} status={req.status}", payload=report)
        brain_updates.append(f"EV-{handover_id}")

        for eid in req.evidence_ids:
            repo.create_link(conn, handover_id, "evidence_of", eid)

        conn.commit()

        if db_path:
            exports_dir = Path(str(db_path)).parent / "exports"
        else:
            exports_dir = Path(__file__).parents[3].joinpath(".brain/exports")
        exports_dir.mkdir(parents=True, exist_ok=True)
        md = _handover_markdown(handover_id, report)
        (exports_dir / "latest-handover.md").write_text(md, encoding="utf-8")

        return BrainHandoverResponse(handover_id=handover_id, report=report, brain_updates=brain_updates)
    finally:
        conn.close()


def _handover_markdown(handover_id: str, report: dict[str, Any]) -> str:
    lines = [f"# Handover {handover_id}", ""]
    lines.append(f"- Task: {report.get('task_id') or '—'}")
    lines.append(f"- Status: {report.get('status')}")
    lines.append(f"- Agent: {report.get('agent_id')}")
    lines.append(f"- Session: {report.get('session_id') or '—'}")
    lines.append("")
    if report.get("completed"):
        lines.append("## Completed")
        for c in report["completed"]:
            lines.append(f"- {c}")
        lines.append("")
    if report.get("failed"):
        lines.append("## Failed")
        for f in report["failed"]:
            lines.append(f"- {f}")
        lines.append("")
    if report.get("discovered"):
        lines.append("## Discovered")
        for d in report["discovered"]:
            lines.append(f"- {d}")
        lines.append("")
    if report.get("remaining"):
        lines.append("## Remaining")
        for r in report["remaining"]:
            lines.append(f"- {r}")
        lines.append("")
    if report.get("next_step"):
        lines.append(f"## Next Step\n{report['next_step']}\n")
    if report.get("evidence_ids"):
        lines.append("## Evidence")
        for eid in report["evidence_ids"]:
            lines.append(f"- {eid}")
        lines.append("")
    lines.append(f"_Generated at {now_iso()}_")
    return "\n".join(lines)
