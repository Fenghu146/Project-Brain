from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from . import repository as repo
from .curator import ModelCuratorAdapter, RuleCurator, deduplicate_check, needs_verification, type_required_fields, validate_status
from .db import get_connection, init_db
from .models import (
    BrainAskRequest,
    BrainAskResponse,
    BrainCurateRequest,
    BrainCurateResponse,
    BrainHandoverRequest,
    BrainHandoverResponse,
    BrainOnboardRequest,
    BrainOnboardResponse,
    BrainRecordRequest,
    BrainRecordResponse,
    BrainReviewApplyRequest,
    BrainReviewApplyResponse,
    BrainReviewListRequest,
    BrainReviewListResponse,
    BrainSnapshotRequest,
    BrainSnapshotResponse,
    IngestRequest,
    content_to_text,
    now_iso,
)
from .search import compute_confidence, ranked_search
from .answer_brain import AnswerBrain, answer_v2


def ensure_db(db_path: str | Path | None = None) -> sqlite3.Connection:
    conn = get_connection(db_path)
    init_db(db_path)
    return conn


def _answer_from_row(row: dict[str, Any]) -> str:
    c = row.get("content")
    if isinstance(c, dict):
        if row.get("type") == "decision":
            return str(c.get("decision") or c.get("reason") or json.dumps(c, ensure_ascii=False)[:200])
        if row.get("type") == "experience":
            return str(c.get("lesson") or c.get("attempt") or c.get("reason") or json.dumps(c, ensure_ascii=False)[:200])
        if row.get("type") == "knowledge":
            return str(c.get("content") or c.get("text") or json.dumps(c, ensure_ascii=False)[:200])
        if row.get("type") == "task":
            return str(c.get("title") or c.get("next_step") or json.dumps(c, ensure_ascii=False)[:200])
        if row.get("type") == "identity":
            return str(c.get("purpose") or c.get("name") or json.dumps(c, ensure_ascii=False)[:200])
        if row.get("type") == "state":
            return str(c.get("current_goal") or c.get("text") or json.dumps(c, ensure_ascii=False)[:200])
        return str(c.get("text") or c.get("content") or json.dumps(c, ensure_ascii=False)[:200])
    return str(c)[:200]


def brain_ingest(req: IngestRequest, db_path: str | Path | None = None) -> dict[str, Any]:
    from .ingestion import ingest_file, ingest_git, ingest_test

    pid = req.project_id
    source = req.source
    if source == "git":
        return ingest_git(project_id=pid, agent_id=req.agent_id, session_id=req.session_id, cwd=req.payload.get("cwd"), db_path=db_path)
    if source == "test":
        return ingest_test(project_id=pid, command=req.payload.get("command", "make test"), agent_id=req.agent_id, session_id=req.session_id, cwd=req.payload.get("cwd"), db_path=db_path, log_path=req.payload.get("log_path"))
    if source == "file":
        return ingest_file(project_id=pid, path=req.payload.get("path", "."), agent_id=req.agent_id, session_id=req.session_id, db_path=db_path)
    conn = ensure_db(db_path)
    try:
        dedup_key = req.payload.get("dedup_key")
        ev_id = repo.create_event(conn, project_id=pid, action=req.payload.get("action", "record"), source=source, result=req.payload.get("result", "observed"), agent_id=req.agent_id, session_id=req.session_id, target=req.payload.get("target"), summary=req.payload.get("summary", source), payload=req.payload, dedup_key=dedup_key)
        conn.commit()
        return {"event_id": ev_id, "warnings": []}
    finally:
        conn.close()


def brain_record(req: BrainRecordRequest, db_path: str | Path | None = None) -> BrainRecordResponse:
    if any((r.origin or "user") == "model_curator" and (r.status in ("verified", "active")) for r in req.records):
        warnings_extra: list[str] = []
        proposals_created: list[str] = []
        conn2 = ensure_db(db_path)
        try:
            for r in req.records:
                if (r.origin or "") == "model_curator" and r.status in ("verified", "active"):
                    warnings_extra.append(f"model_curator cannot request {r.status}, downgraded to proposal")
                    repo.create_proposal(conn2, project_id=req.project_id, action="create_memory", payload={"type": r.type, "content": r.content, "status": r.status}, reason="model_curator verified request must be reviewed", source_event_ids=[], confidence=r.confidence, curator_version="model-v0", origin="model_curator")
                    r.status = "proposed"
            conn2.commit()
        finally:
            conn2.close()
    conn = ensure_db(db_path)
    pid = req.project_id
    accepted: list[str] = []
    deduplicated: list[str] = []
    needs_verif: list[str] = []
    warnings: list[str] = []
    state_updates: list[str] = []
    duplicate_of: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []

    existing = repo.list_memories(conn, project_id=pid, limit=500)
    existing_texts = [content_to_text(m["content"]) for m in existing]
    existing_by_text: dict[str, str] = {content_to_text(m["content"]): m["id"] for m in existing}

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
                        project_id=pid,
                    )
                    accepted.append(ev_id)
                    repo.create_event(conn, action="record", agent_id=req.agent_id, session_id=req.session_id, summary=f"record evidence {ev_id}", payload={"evidence_id": ev_id}, project_id=pid)
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
                        project_id=pid,
                    )
                    accepted.append(ev_id)
                continue

            if mem_type not in ("identity", "state", "knowledge", "experience", "decision", "task"):
                warnings.append(f"unknown type {mem_type}, treated as knowledge")
                mem_type = "knowledge"

            content = rec.content
            text = content_to_text(content)
            has_evidence = bool(rec.evidence or rec.evidence_ids)
            evidence_ids = list(rec.evidence_ids or [])

            if isinstance(content, dict):
                missing = type_required_fields(mem_type, content)
                if missing:
                    warnings.append(f"{mem_type} missing required fields: {', '.join(missing)}")
                if mem_type == "decision" and content.get("decision"):
                    for m in existing:
                        if m["type"] == "decision" and isinstance(m["content"], dict) and m["content"].get("decision") == content["decision"] and m["id"] != content.get("id"):
                            warnings.append(f"decision duplicate_of {m['id']}")
                            duplicate_of.append({"new": text[:80], "duplicate_of": m["id"], "similarity": 1.0, "action": "kept_both"})

            is_dup = deduplicate_check(text, existing_texts)
            if is_dup:
                deduplicated.append(text[:80])
                warnings.append(f"possible duplicate for {mem_type}: {text[:60]}")
                dup_id = existing_by_text.get(text)
                if dup_id:
                    duplicate_of.append({"new": text[:80], "duplicate_of": dup_id, "similarity": 0.85, "action": "kept_both"})

            if evidence_ids:
                for eid in evidence_ids:
                    ev = repo.get_evidence(conn, eid, project_id=pid)
                    if ev is None:
                        warnings.append(f"evidence {eid} not found in project {pid}; record will stay proposed")
                        has_evidence = False

            status, task_status, ws = validate_status(mem_type, rec.status, rec.task_status, has_evidence)
            warnings.extend(ws)
            if mem_type in ("decision", "experience") and not has_evidence and status in ("verified", "active"):
                warnings.append(f"{mem_type} without valid evidence cannot be {status}, set to proposed")
                status = "proposed"

            mem_id = repo.create_memory(
                conn,
                mem_type=mem_type,
                content=content,
                status=status,
                task_status=task_status,
                confidence=rec.confidence,
                tags=rec.tags,
                created_by=req.agent_id,
                project_id=pid,
                valid_from=rec.valid_from,
                valid_until=rec.valid_until,
                branch=rec.branch,
                commit_hash=rec.commit_hash,
                verification_due_at=rec.verification_due_at,
                origin=rec.origin or "user",
            )
            accepted.append(mem_id)
            existing_texts.append(text)
            existing_by_text[text] = mem_id

            if rec.evidence:
                for ev in rec.evidence:
                    ev_id = repo.create_evidence(
                        conn,
                        ev_type=ev.get("type", "observed"),
                        source=ev.get("source", "unknown"),
                        description=ev.get("description"),
                        metadata=ev.get("metadata"),
                        status="observed",
                        project_id=pid,
                    )
                    repo.create_link(conn, mem_id, "evidence_of", ev_id, project_id=pid)
                    accepted.append(ev_id)
                    evidence_ids.append(ev_id)

            for eid in evidence_ids:
                if eid not in (rec.evidence_ids or []):
                    continue
                repo.create_link(conn, mem_id, "evidence_of", eid, project_id=pid)

            if mem_type == "decision" and isinstance(content, dict) and content.get("decision"):
                for m in existing:
                    if m["type"] != "decision" or m["id"] == mem_id:
                        continue
                    mc = m["content"]
                    if isinstance(mc, dict) and mc.get("decision") and mc["decision"] != content["decision"]:
                        same_topic = bool(set((content.get("tags") or []) or (rec.tags or [])) & set(m.get("tags") or []))
                        if same_topic or content["decision"][:8] in content_to_text(mc):
                            repo.create_link(conn, mem_id, "conflicts_with", m["id"], project_id=pid)
                            conflicts.append({"from": mem_id, "to": m["id"], "relation": "conflicts_with"})

            if needs_verification(mem_type, status, has_evidence):
                needs_verif.append(mem_id)

            if mem_type == "state":
                state_updates.append(mem_id)

            repo.create_event(conn, action="record", agent_id=req.agent_id, session_id=req.session_id, summary=f"record {mem_type} {mem_id}", payload={"memory_id": mem_id, "type": mem_type}, project_id=pid)

        conn.commit()
    finally:
        conn.close()

    return BrainRecordResponse(
        accepted=accepted,
        deduplicated=deduplicated,
        needs_verification=needs_verif,
        warnings=warnings,
        state_updates=state_updates,
        duplicate_of=duplicate_of,
        conflicts=conflicts,
    )


def brain_ask(req: BrainAskRequest, db_path: str | Path | None = None) -> BrainAskResponse:
    conn = ensure_db(db_path)
    pid = req.project_id
    try:
        match_mode, results, matches = ranked_search(conn, req.question, scope=req.scope, limit=req.limit, project_id=pid, as_of_commit=req.as_of_commit, as_of_time=req.as_of_time)
        # filter by valid window if as_of_time
        if req.as_of_time:
            try:
                from datetime import datetime, timezone

                as_of = datetime.fromisoformat(req.as_of_time.replace("Z", "+00:00"))
                filtered = []
                for r in results:
                    vf = r.get("valid_from")
                    vu = r.get("valid_until")
                    if vf:
                        try:
                            if datetime.fromisoformat(vf.replace("Z", "+00:00")) > as_of:
                                continue
                        except Exception:
                            pass
                    if vu:
                        try:
                            if datetime.fromisoformat(vu.replace("Z", "+00:00")) < as_of:
                                continue
                        except Exception:
                            pass
                    filtered.append(r)
                results = filtered
            except Exception:
                pass
        # stale detection: valid_until passed
        stale_facts: list[dict[str, Any]] = []
        fresh: list[dict[str, Any]] = []
        for r in results:
            vu = r.get("valid_until")
            is_stale = False
            if vu:
                try:
                    from datetime import datetime, timezone

                    if datetime.fromisoformat(vu.replace("Z", "+00:00")) < datetime.now(timezone.utc):
                        is_stale = True
                except Exception:
                    pass
            if is_stale:
                r2 = dict(r)
                stale_facts.append({"id": r2["id"], "type": r2["type"], "status": r2["status"], "kind": "stale_fact", "provenance": [lk["to_id"] for lk in repo.get_links(conn, from_id=r2["id"], project_id=pid)], "verification_suggestion": f"重新验证 {r2['id']} 的时效"})
            else:
                fresh.append(r)
        if stale_facts and not req.include_proposals:
            results = fresh
        facts = [{"id": r["id"], "type": r["type"], "status": r["status"], "task_status": r.get("task_status"), "kind": "fact", "provenance": [lk["to_id"] for lk in repo.get_links(conn, from_id=r["id"], project_id=pid)]} for r in results]

        evidence_list: list[dict[str, Any]] = []
        if req.include_evidence:
            for r in results:
                links = repo.get_links(conn, from_id=r["id"], project_id=pid)
                for lk in links:
                    if lk["relation"] == "evidence_of":
                        ev = repo.get_evidence(conn, lk["to_id"], project_id=pid)
                        if ev:
                            evidence_list.append(ev)

        proposals_out: list[dict[str, Any]] = []
        if req.include_proposals:
            props = repo.list_proposals(conn, project_id=pid, status="pending", limit=req.limit)
            for p in props:
                if req.as_of_commit and p.get("payload", {}).get("commit_hash") and p["payload"]["commit_hash"] != req.as_of_commit:
                    continue
                proposals_out.append({**p, "kind": "proposal", "provenance": p.get("source_event_ids", []), "verification_suggestion": p.get("verification_suggestion")})

        if results:
            answer = "；".join(_answer_from_row(r) for r in results[:3])
        elif proposals_out:
            answer = f"未找到已确认事实，但有 {len(proposals_out)} 条待审建议。"
        else:
            answer = "未找到相关记录。"

        uncertainties: list[str] = []
        suggestions: list[str] = []
        if not results and not proposals_out:
            uncertainties.append("无匹配记录，建议检查关键词或补充记录。")
            if match_mode == "none":
                uncertainties.append("检索未命中任何候选，已应用相关度阈值过滤低相关结果。")
            suggestions.append("尝试使用不同的关键词或同义词查询")
            suggestions.append("使用 brain record --type knowledge --content '{\"content\":\"你的知识内容\"}' 补充相关知识")
            suggestions.append("使用 brain record --type decision --content '{\"decision\":\"决策内容\",\"reason\":\"决策原因\"}' 记录重要决策")
            all_props = repo.list_proposals(conn, project_id=pid, status="pending", limit=5)
            if all_props:
                suggestions.append(f"有 {len(all_props)} 条待审提案，使用 brain review-list 查看")
        else:
            has_unverified = any(r["status"] in ("draft", "proposed", "observed") for r in results)
            if has_unverified:
                uncertainties.append("部分结果尚未验证，需进一步确认。")
                suggestions.append("使用 brain verify --id <id> --action verify 验证重要记录")
            if not evidence_list and results:
                uncertainties.append("相关记录缺少可验证证据。")
                suggestions.append("使用 brain record --type evidence --content '{\"type\":\"test_result\",\"source\":\"测试文件路径\"}' 添加证据")
            low_scores = [m for m in matches if m["score"] < 0.22]
            if low_scores:
                uncertainties.append(f"{len(low_scores)} 条结果相关度较低，仅作弱命中。")
                suggestions.append("尝试更具体的关键词或使用 --scope 参数限定范围")
            if stale_facts:
                uncertainties.append(f"{len(stale_facts)} 条记录已过期，已降级为 stale_fact。")
                suggestions.append("使用 brain record 更新过期记录的状态或内容")
        
        if not suggestions:
            suggestions.append("使用 brain onboard 获取项目完整上下文")
            suggestions.append("使用 brain status 查看项目整体状态")

        confidence = compute_confidence(results, len(evidence_list))
        return BrainAskResponse(answer=answer, facts=facts, evidence=evidence_list, uncertainties=uncertainties, confidence=confidence, match_mode=match_mode, matches=matches, proposals=proposals_out, stale_facts=stale_facts, suggestions=suggestions)
    finally:
        conn.close()


def brain_ask_v2(req: BrainAskRequest, db_path: str | Path | None = None) -> dict[str, Any]:
    """v0.6 enhanced ask using AnswerBrain."""
    result = answer_v2(
        question=req.question,
        project_id=req.project_id,
        agent_id=req.agent_id,
        session_id=req.session_id,
        scope=req.scope,
        limit=req.limit,
        include_proposals=req.include_proposals,
        as_of_commit=req.as_of_commit,
        as_of_time=req.as_of_time,
        db_path=db_path,
    )
    return result.model_dump(mode="json")


def brain_feedback(req, db_path: str | Path | None = None):  # type: ignore[no-untyped-def]
    from .db import get_connection, init_db
    import json as _json

    conn = get_connection(db_path)
    init_db(db_path)
    try:
        import uuid

        fid = f"FB-{uuid.uuid4().hex[:8]}"
        ts = now_iso()
        conn.execute(
            "INSERT INTO answer_feedback (id, project_id, question, answer_claim_ids, intent, confidence, verdict, corrected_text, agent_id, session_id, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (fid, req.project_id, req.question, _json.dumps(req.answer_claim_ids or [], ensure_ascii=False), req.intent, req.confidence, req.verdict, req.corrected_text, req.agent_id, req.session_id, ts),
        )
        # Also write an Event (feedback does not modify Memory)
        from .repository import create_event

        eid = create_event(conn, action="feedback", agent_id=req.agent_id, session_id=req.session_id, summary=f"feedback {req.verdict}: {req.question[:60]}", payload={"question": req.question, "verdict": req.verdict, "intent": req.intent, "feedback_id": fid}, project_id=req.project_id, source="feedback", result=req.verdict)
        conn.commit()
        from .models import BrainFeedbackResponse

        return BrainFeedbackResponse(feedback_id=fid, event_id=eid)
    finally:
        conn.close()


def brain_onboard(req: BrainOnboardRequest, db_path: str | Path | None = None) -> BrainOnboardResponse:
    conn = ensure_db(db_path)
    pid = req.project_id
    try:
        identities = repo.list_memories(conn, project_id=pid, mem_type="identity", limit=1)
        states = repo.list_memories(conn, project_id=pid, mem_type="state", limit=1)
        tasks_all = repo.list_memories(conn, project_id=pid, mem_type="task", limit=20)
        decisions = repo.list_memories(conn, project_id=pid, mem_type="decision", limit=5)
        experiences = repo.list_memories(conn, project_id=pid, mem_type="experience", limit=5)
        handovers = repo.list_handovers(conn, project_id=pid, limit=1)
        pending_reviews = len(repo.list_proposals(conn, project_id=pid, status="pending", limit=100))
        snapshots = repo.list_snapshots(conn, project_id=pid, limit=1)
        basis_commit = snapshots[0].get("basis_commit") if snapshots else None
        try:
            from .project_model import build_model

            model, _, _ = build_model(conn, pid)
            stale_context = model.get("stale", []) if isinstance(model, dict) else []
            verification_suggestions = [f"验证 {s['id']}: {s['reason']}" for s in stale_context[:5]]
        except Exception:
            stale_context = []
            verification_suggestions = []

        blocked_tasks = [t for t in tasks_all if t.get("task_status") == "blocked"]
        in_progress_tasks = [t for t in tasks_all if t.get("task_status") == "in_progress"]
        active_tasks = blocked_tasks + in_progress_tasks
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

        def _task_brief(t: dict[str, Any]) -> dict[str, Any]:
            c = t.get("content") if isinstance(t.get("content"), dict) else {}
            return {"id": t["id"], "title": c.get("title") or str(t["content"])[:80], "task_status": t.get("task_status"), "status": t.get("status")}

        brief["blocked_tasks"] = [_task_brief(t) for t in blocked_tasks[:3]]
        brief["active_tasks"] = [_task_brief(t) for t in in_progress_tasks[:3]]
        brief["important_decisions"] = [{"id": d["id"], "summary": (d["content"].get("decision") if isinstance(d["content"], dict) else str(d["content"])[:80])} for d in important_decisions[:3]]
        brief["known_failures"] = [{"id": e["id"], "summary": (e["content"].get("lesson") or e["content"].get("attempt") if isinstance(e["content"], dict) else str(e["content"])[:80])} for e in known_failures[:3]]

        if handovers:
            h = handovers[0]
            brief["latest_handover"] = {"id": h["id"], "status": h["status"], "task_id": h["task_id"], "summary": (h["report"].get("next_step") or h["report"].get("remaining") or "")}

        if states and isinstance(states[0]["content"], dict):
            c = states[0]["content"]
            brief["recommended_next_step"] = c.get("recommended_next_step") or (tasks_all[0]["content"].get("next_step") if tasks_all and isinstance(tasks_all[0]["content"], dict) else None) or "暂无明确下一步。"
        else:
            brief["recommended_next_step"] = "暂无明确下一步。"

        if req.focus:
            _, focused, _ = ranked_search(conn, req.focus, limit=5, project_id=pid)
            brief["focus_matches"] = [{"id": r["id"], "type": r["type"], "status": r["status"], "task_status": r.get("task_status")} for r in focused]

        missing: list[str] = []
        context_suggestions: list[str] = []
        if not identities:
            missing.append("identity")
            context_suggestions.append("建议使用 brain record --type identity --content '{\"name\":\"项目名称\",\"purpose\":\"项目目的\"}' 添加项目身份")
        if not states:
            missing.append("state")
            context_suggestions.append("建议使用 brain record --type state --content '{\"current_goal\":\"当前目标\",\"blockers\":[],\"open_questions\":[]}' 添加项目状态")
        if not tasks_all:
            missing.append("tasks")
            context_suggestions.append("建议使用 brain record --type task --content '{\"title\":\"任务标题\",\"remaining\":[\"待办事项\"],\"next_step\":\"下一步\"}' 添加任务")
        if not decisions:
            missing.append("decisions")
            context_suggestions.append("建议使用 brain record --type decision --content '{\"decision\":\"决策内容\",\"reason\":\"决策原因\"}' 添加重要决策")
        if not handovers:
            missing.append("handover")
            context_suggestions.append("建议在工作交接时使用 brain handover 创建交接报告")
        brief["missing_context"] = missing
        if context_suggestions:
            brief["context_suggestions"] = context_suggestions

        source_ids: list[str] = []
        for lst in [identities, states, blocked_tasks, in_progress_tasks, important_decisions, known_failures, handovers]:
            for m in lst[:3]:
                mid = m.get("id")
                if mid and mid not in source_ids:
                    source_ids.append(mid)

        evidence_ids: list[str] = []
        for sid in source_ids:
            for lk in repo.get_links(conn, from_id=sid, project_id=pid):
                if lk["relation"] == "evidence_of" and lk["to_id"] not in evidence_ids:
                    evidence_ids.append(lk["to_id"])

        brief["pending_reviews"] = pending_reviews
        brief["stale_context"] = stale_context[:5] if stale_context else []
        brief["verification_suggestions"] = verification_suggestions
        if basis_commit:
            brief["basis_commit"] = basis_commit
        try:
            from .project_model import build_model

            model2, _, _ = build_model(conn, pid)
            brief["project_model_summary"] = {k: (len(v) if isinstance(v, list) else (1 if v else 0)) for k, v in model2.items() if k != "provenance"}
        except Exception:
            pass

        all_facts = identities + states + tasks_all + decisions + experiences
        ev_count = len(repo.list_evidence(conn, project_id=pid, limit=100))
        confidence = compute_confidence(all_facts, ev_count)

        if req.token_budget:
            brief = _truncate_brief(brief, req.token_budget)

        return BrainOnboardResponse(project_id=req.project_id, generated_at=now_iso(), brief=brief, source_ids=source_ids, evidence_ids=evidence_ids, missing_context=missing, pending_reviews=pending_reviews, stale_context=stale_context[:5], verification_suggestions=verification_suggestions, basis_commit=basis_commit, confidence=confidence)
    finally:
        conn.close()


def brain_curate(req: BrainCurateRequest, db_path: str | Path | None = None) -> BrainCurateResponse:
    conn = ensure_db(db_path)
    mode = req.mode
    if mode == "model":
        try:
            adapter = ModelCuratorAdapter()
            if not getattr(adapter, "enabled", False):
                raise RuntimeError("model curator disabled")
            events = repo.list_events(conn, project_id=req.project_id, limit=50)
            props = adapter.generate(events, req.project_id)
            if props:
                conn.commit()
                return BrainCurateResponse(created=[p["id"] for p in props], warnings=[])
            raise RuntimeError("model returned nothing")
        except Exception as e:
            warnings = [f"model curator fallback: {e}"]
            rule = RuleCurator()
            created, w2 = rule.curate(conn, req.project_id, event_ids=req.event_ids)
            conn.commit()
            warnings.extend(w2)
            return BrainCurateResponse(created=created, warnings=warnings)
    rule = RuleCurator()
    created, warnings = rule.curate(conn, req.project_id, event_ids=req.event_ids)
    conn.commit()
    return BrainCurateResponse(created=created, warnings=warnings)


def brain_review_list(req: BrainReviewListRequest, db_path: str | Path | None = None) -> BrainReviewListResponse:
    conn = ensure_db(db_path)
    try:
        proposals = repo.list_proposals(conn, project_id=req.project_id, status=req.status, limit=req.limit)
        return BrainReviewListResponse(proposals=proposals)
    finally:
        conn.close()


def brain_review_apply(req: BrainReviewApplyRequest, db_path: str | Path | None = None) -> BrainReviewApplyResponse:
    conn = ensure_db(db_path)
    pid = req.project_id
    try:
        prop = repo.get_proposal(conn, req.proposal_id, project_id=pid)
        if prop is None:
            raise ValueError(f"proposal {req.proposal_id} not found in {pid}")
        if prop["project_id"] != pid:
            raise ValueError("project_id mismatch")
        if prop["status"] == "approved":
            raise ValueError(f"proposal {req.proposal_id} already approved")
        for eid in prop.get("source_event_ids", []):
            found = any(e["id"] == eid for e in repo.list_events(conn, project_id=pid, limit=200))
            if not found:
                raise ValueError(f"source event {eid} no longer exists, re-curate required")
        target_id = prop.get("target_id")
        if target_id:
            mem = repo.get_memory(conn, target_id, project_id=pid)
            if mem and mem.get("updated_at") and prop.get("created_at") and mem["updated_at"] > prop["created_at"]:
                repo.update_proposal(conn, req.proposal_id, status="superseded", reviewer=req.reviewer, superseded_by=target_id, project_id=pid)
                ev_id = repo.create_event(conn, project_id=pid, action="review", agent_id=req.reviewer, summary=f"review {req.proposal_id} superseded (target updated)", payload={"proposal_id": req.proposal_id, "action": "superseded"}, source="review", result="superseded")
                conn.commit()
                raise ValueError(f"target {target_id} changed after proposal, marked superseded; re-curate")
        new_status = req.action
        repo.update_proposal(conn, req.proposal_id, status=new_status, reviewer=req.reviewer, project_id=pid)
        applied_event_id = None
        if new_status == "approved":
            action = prop["action"]
            payload = prop.get("payload") or {}
            if action == "create_memory":
                mtype = payload.get("type", "knowledge")
                repo.create_memory(conn, mem_type=mtype, content=payload.get("content", {}), status=payload.get("status", "proposed"), tags=payload.get("tags"), created_by=req.reviewer, project_id=pid, origin="rule_curator")
            elif action == "update_memory" and target_id:
                repo.update_memory(conn, target_id, content=payload.get("content"), status=payload.get("status"), project_id=pid)
            elif action == "verify_memory" and target_id:
                repo.update_memory(conn, target_id, status="verified", project_id=pid)
            elif action == "invalidate_memory" and target_id:
                repo.update_memory(conn, target_id, status="invalid", project_id=pid)
            elif action == "create_link":
                repo.create_link(conn, payload.get("from_id"), payload.get("relation", "related_to"), payload.get("to_id"), project_id=pid)
            elif action == "create_task":
                repo.create_memory(conn, mem_type="task", content=payload.get("content", {}), status="active", task_status="in_progress", created_by=req.reviewer, project_id=pid)
            applied_event_id = repo.create_event(conn, project_id=pid, action="review", agent_id=req.reviewer, summary=f"review {req.proposal_id} {new_status}", payload={"proposal_id": req.proposal_id, "action": new_status, "reason": req.reason}, source="review", result=new_status)
        else:
            applied_event_id = repo.create_event(conn, project_id=pid, action="review", agent_id=req.reviewer, summary=f"review {req.proposal_id} {new_status}", payload={"proposal_id": req.proposal_id, "action": new_status}, source="review", result=new_status)
        conn.commit()
        prop2 = repo.get_proposal(conn, req.proposal_id, project_id=pid)
        return BrainReviewApplyResponse(proposal=prop2 or prop, applied_event_id=applied_event_id)
    finally:
        conn.close()


def brain_snapshot(req: BrainSnapshotRequest, db_path: str | Path | None = None) -> BrainSnapshotResponse:
    conn = ensure_db(db_path)
    try:
        from .project_model import create_snapshot, build_model

        sid = create_snapshot(conn, req.project_id, basis_commit=req.basis_commit, basis_branch=req.basis_branch)
        conn.commit()
        snap = repo.get_snapshot(conn, sid, project_id=req.project_id)
        assert snap is not None
        return BrainSnapshotResponse(snapshot_id=snap["id"], model_json=snap["model_json"], source_ids=snap["source_ids"], confidence=snap["confidence"])
    finally:
        conn.close()


def brain_rebuild_snapshot(snapshot_id: str, project_id: str, db_path: str | Path | None = None) -> BrainSnapshotResponse:
    conn = ensure_db(db_path)
    try:
        from .project_model import rebuild_snapshot

        snap = rebuild_snapshot(conn, snapshot_id, project_id=project_id)
        conn.commit()
        return BrainSnapshotResponse(snapshot_id=snap["id"], model_json=snap["model_json"], source_ids=snap["source_ids"], confidence=snap["confidence"])
    finally:
        conn.close()


def _truncate_brief(brief: dict[str, Any], token_budget: int) -> dict[str, Any]:
    char_budget = token_budget * 4
    order = ["missing_context", "focus_matches", "known_failures", "important_decisions", "blocked_tasks", "active_tasks"]
    text = json.dumps(brief, ensure_ascii=False)
    if len(text) <= char_budget:
        return brief
    brief = dict(brief)
    for k in order:
        if k in brief:
            if isinstance(brief[k], list) and len(brief[k]) > 1:
                brief[k] = brief[k][:1]
            elif k == "focus_matches":
                brief.pop(k, None)
            text = json.dumps(brief, ensure_ascii=False)
            if len(text) <= char_budget:
                return brief
    return brief


def brain_handover(req: BrainHandoverRequest, db_path: str | Path | None = None) -> BrainHandoverResponse:
    conn = ensure_db(db_path)
    pid = req.project_id
    try:
        if req.task_id:
            task = repo.get_memory(conn, req.task_id, project_id=pid)
            if task is None:
                raise ValueError(f"task {req.task_id} not found in project {pid}")
            if task.get("type") != "task":
                raise ValueError(f"{req.task_id} is not a task (type={task.get('type')})")

        for eid in req.evidence_ids:
            ev = repo.get_evidence(conn, eid, project_id=pid)
            if ev is None:
                raise ValueError(f"evidence {eid} not found in project {pid}")

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
        handover_id = repo.create_handover(conn, task_id=req.task_id, agent_id=req.agent_id, session_id=req.session_id, status=req.status, report=report, project_id=pid)
        brain_updates: list[str] = [handover_id]

        if req.task_id:
            task = repo.get_memory(conn, req.task_id, project_id=pid)
            assert task is not None
            content = dict(task["content"]) if isinstance(task["content"], dict) else {"text": str(task["content"])}
            if req.completed:
                content["completed"] = content.get("completed", []) + req.completed
            if req.remaining:
                content["remaining"] = req.remaining
            if req.recommended_next_step:
                content["next_step"] = req.recommended_next_step
            task_status_map = {"completed": "completed", "partial": "in_progress", "failed": "blocked"}
            new_task_status = task_status_map.get(req.status, "in_progress")
            repo.update_memory(conn, req.task_id, content=content, task_status=new_task_status, project_id=pid)
            brain_updates.append(req.task_id)

        states = repo.list_memories(conn, project_id=pid, mem_type="state", limit=1)
        if states:
            s = states[0]
            c = dict(s["content"]) if isinstance(s["content"], dict) else {"text": str(s["content"])}
            c["recent_changes"] = (c.get("recent_changes", []) + req.completed)[:10]
            if req.remaining:
                c["open_questions"] = req.remaining[:5]
            if req.recommended_next_step:
                c["recommended_next_step"] = req.recommended_next_step
            if req.status == "failed" and req.failed:
                c["blockers"] = (c.get("blockers", []) + req.failed)[:5]
            repo.update_memory(conn, s["id"], content=c, project_id=pid)
            brain_updates.append(s["id"])

        repo.create_event(conn, action="handover", agent_id=req.agent_id, session_id=req.session_id, target=req.task_id, summary=f"handover {handover_id} status={req.status}", payload=report, project_id=pid)
        brain_updates.append(f"EV-{handover_id}")

        for eid in req.evidence_ids:
            repo.create_link(conn, handover_id, "evidence_of", eid, project_id=pid)

        session_event_ids = [e["id"] for e in repo.list_events(conn, project_id=pid, limit=100) if e.get("session_id") == req.session_id] if req.session_id else []
        pending_count = len(repo.list_proposals(conn, project_id=pid, status="pending", limit=100))
        verification_suggestions: list[str] = []
        try:
            from .project_model import build_model

            m, _, _ = build_model(conn, pid)
            for s in (m.get("stale", [])[:3] if isinstance(m, dict) else []):
                verification_suggestions.append(f"验证 {s['id']}: {s['reason']}")
        except Exception:
            pass
        basis_commit = None
        try:
            import subprocess

            basis_commit = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True, timeout=3).strip()
        except Exception:
            pass
        model_snapshot_id = None
        try:
            from .project_model import create_snapshot

            model_snapshot_id = create_snapshot(conn, pid, basis_commit=basis_commit)
        except Exception:
            pass
        report["session_event_ids"] = session_event_ids
        report["pending_proposals_count"] = pending_count
        report["verification_suggestions"] = verification_suggestions
        report["basis_commit"] = basis_commit
        report["model_snapshot_id"] = model_snapshot_id

        conn.commit()

        if db_path:
            exports_dir = Path(str(db_path)).parent / "exports"
        else:
            exports_dir = Path(__file__).parents[3].joinpath(".brain/exports")
        exports_dir.mkdir(parents=True, exist_ok=True)
        md = _handover_markdown(handover_id, report)
        (exports_dir / "latest-handover.md").write_text(md, encoding="utf-8")

        return BrainHandoverResponse(handover_id=handover_id, report=report, brain_updates=brain_updates, pending_proposals_count=pending_count, verification_suggestions=verification_suggestions, basis_commit=basis_commit, model_snapshot_id=model_snapshot_id)
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
