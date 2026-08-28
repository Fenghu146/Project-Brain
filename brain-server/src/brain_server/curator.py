from __future__ import annotations

import json
from typing import Any

from . import repository as repo
from .models import VALID_MEMORY_STATUSES, VALID_TASK_STATUSES

CURATOR_VERSION = "rule-v1"


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


def classify_record(content: Any) -> str:
    if isinstance(content, str):
        return "knowledge"
    if not isinstance(content, dict):
        return "knowledge"
    if "decision" in content and "reason" in content:
        return "decision"
    if "attempt" in content and "result" in content:
        return "experience"
    if "title" in content:
        return "task"
    return "knowledge"


class ModelCuratorAdapter:
    version = "model-v0"
    enabled = False

    def generate(self, events: list[dict[str, Any]], project_id: str) -> list[dict[str, Any]]:
        return []


class RuleCurator:
    version = CURATOR_VERSION

    def curate(self, conn, project_id: str, event_ids: list[str] | None = None) -> tuple[list[str], list[str]]:
        warnings: list[str] = []
        created: list[str] = []
        events = repo.list_events(conn, project_id=project_id, limit=100)
        if event_ids:
            events = [e for e in events if e["id"] in event_ids]
        existing = repo.list_memories(conn, project_id=project_id, limit=500)
        existing_texts = [json.dumps(m["content"], ensure_ascii=False) for m in existing]

        seen_proposal_keys: set[str] = set()
        for r in repo.list_proposals(conn, project_id=project_id, limit=1000):
            seen_proposal_keys.add(f"{r['action']}|{r['source_event_ids']}")

        for ev in events:
            action = ev.get("action")
            result = ev.get("result")
            summary = ev.get("summary") or ""
            payload = ev.get("payload") or {}
            key_base = f"create_memory|{[ev['id']]}"

            if action == "run_test" and result == "failed":
                if key_base in seen_proposal_keys:
                    continue
                pid = repo.create_proposal(
                    conn,
                    project_id=project_id,
                    action="create_memory",
                    payload={"type": "experience", "content": {"task": payload.get("command", "test"), "attempt": payload.get("command", "test"), "result": "failed", "reason": summary[:300], "lesson": "需复现并定位失败原因"}, "status": "proposed"},
                    reason="测试失败应沉淀为失败经验与风险",
                    source_event_ids=[ev["id"]],
                    affected_ids=[],
                    risk="失败原因未归因，可能重复踩坑",
                    verification_suggestion="复现失败并补充日志/evidence 后再审核",
                    confidence=0.8,
                    curator_version=self.version,
                    origin="rule_curator",
                )
                created.append(pid)
                seen_proposal_keys.add(key_base)
            elif action == "run_test" and result == "passed":
                if key_base in seen_proposal_keys:
                    continue
                pid = repo.create_proposal(
                    conn,
                    project_id=project_id,
                    action="create_memory",
                    payload={"type": "knowledge", "content": {"content": f"测试通过：{payload.get('command','')} @ {payload.get('commit','')[:7] if payload.get('commit') else ''}", "scope": "test"}, "status": "proposed"},
                    reason="测试通过可作为知识/经验候选",
                    source_event_ids=[ev["id"]],
                    verification_suggestion="确认该测试覆盖关键路径后再审核",
                    confidence=0.55,
                    curator_version=self.version,
                )
                created.append(pid)
                seen_proposal_keys.add(key_base)

            changed = payload.get("changed_files") or []
            if isinstance(changed, list) and len(changed) >= 3 and action == "commit":
                pid = repo.create_proposal(
                    conn,
                    project_id=project_id,
                    action="create_memory",
                    payload={"type": "knowledge", "content": {"content": f"热点模块：近次提交涉及 {len(changed)} 个文件", "scope": "architecture"}, "status": "proposed"},
                    reason="同一提交涉及多模块，可能为架构热点",
                    source_event_ids=[ev["id"]],
                    verification_suggestion="回归相关模块测试",
                    confidence=0.55,
                    curator_version=self.version,
                )
                created.append(pid)

            for mem in existing:
                if mem["type"] == "decision" and mem.get("commit_hash") and payload.get("commit") and mem["commit_hash"] != payload.get("commit"):
                    if any(f in (payload.get("changed_files") or []) for f in ["project-brain-v0.3.md", "README.md"]) or True:
                        pass
                text = json.dumps(mem["content"], ensure_ascii=False)
                if action == "run_test" and deduplicate_check(summary, [text], threshold=0.9):
                    pid = repo.create_proposal(
                        conn,
                        project_id=project_id,
                        action="create_link",
                        payload={"from_id": ev["id"], "relation": "related_to", "to_id": mem["id"]},
                        reason="事件与已有记忆高度相似，可能重复",
                        source_event_ids=[ev["id"]],
                        affected_ids=[mem["id"]],
                        confidence=0.72,
                        curator_version=self.version,
                    )
                    created.append(pid)
                    break

        # evidence health: evidence source path missing -> verify proposal
        for evi in repo.list_evidence(conn, project_id=project_id, limit=50):
            src = evi.get("source") or ""
            if src and "/" in src and not src.startswith("http"):
                from pathlib import Path

                p = Path(src)
                if not p.exists() and not Path("/Users/fenghui/Desktop/Project Brain") .joinpath(src).exists():
                    linked = repo.get_links(conn, to_id=evi["id"], project_id=project_id)
                    for lk in linked:
                        if lk["relation"] == "evidence_of":
                            mem_id = lk["from_id"]
                            pid = repo.create_proposal(
                                conn,
                                project_id=project_id,
                                action="verify_memory",
                                target_type="memory",
                                target_id=mem_id,
                                payload={"status": "proposed"},
                                reason=f"证据路径失效：{src}",
                                source_event_ids=[],
                                source_evidence_ids=[evi["id"]],
                                affected_ids=[mem_id],
                                verification_suggestion=f"检查证据 {src} 是否仍可追溯，必要时重新生成",
                                confidence=0.7,
                                curator_version=self.version,
                            )
                            created.append(pid)
                            break

        return created, warnings
