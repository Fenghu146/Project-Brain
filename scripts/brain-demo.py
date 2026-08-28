#!/usr/bin/env python3
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "brain-server/src"))

from brain_server.db import init_db
from brain_server.models import BrainAskRequest, BrainHandoverRequest, BrainOnboardRequest, BrainRecordRequest, RecordInput
from brain_server.protocol import brain_ask, brain_handover, brain_onboard, brain_record


def assert_eq(a, b, msg):
    if a != b:
        print(f"ASSERT FAIL: {msg} — {a!r} != {b!r}")
        sys.exit(1)


def assert_true(cond, msg):
    if not cond:
        print(f"ASSERT FAIL: {msg}")
        sys.exit(1)


def run_demo(db_path: str, project_id: str = "project-brain") -> None:
    print(f"=== Project Brain v0.2 Demo — project={project_id} db={db_path} ===\n")

    print("1. onboard before work")
    r1 = brain_onboard(BrainOnboardRequest(project_id=project_id, agent_id="agent-a", session_id="session-12", focus="v0.2"), db_path=db_path)
    print(json.dumps(r1.model_dump(), ensure_ascii=False, indent=2))
    assert_true(r1.brief.get("identity") != "未设置项目身份。", "identity should exist")
    print()

    print("2. agent-a record + handover (partial)")
    r2 = brain_record(
        BrainRecordRequest(
            project_id=project_id,
            agent_id="agent-a",
            session_id="session-12",
            records=[
                RecordInput(type="evidence", content={"type": "test_result", "source": "tests/demo.log", "description": "v0.2 demo log"}),
            ],
        ),
        db_path=db_path,
    )
    print(json.dumps(r2.model_dump(), ensure_ascii=False, indent=2))

    r3 = brain_handover(
        BrainHandoverRequest(
            project_id=project_id,
            agent_id="agent-a",
            session_id="session-12",
            task_id="T-001",
            status="partial",
            completed=["完成 v0.2 隔离自检"],
            discovered=["FTS 跨项目污染已用阈值与 AND 回退修复"],
            remaining=["补充 scope 与无关查询回归"],
            recommended_next_step="由下一个 Agent 验证 ask 阈值与 scope",
            evidence_ids=[],
        ),
        db_path=db_path,
    )
    print(json.dumps(r3.model_dump(), ensure_ascii=False, indent=2))
    print()

    print("3. agent-b onboard — should see task with remaining/next_step")
    r4 = brain_onboard(BrainOnboardRequest(project_id=project_id, agent_id="agent-b", session_id="session-13", focus="交接"), db_path=db_path)
    print(json.dumps(r4.model_dump(), ensure_ascii=False, indent=2))
    assert_true(r4.brief["active_tasks"] or r4.brief["blocked_tasks"], "should have active/blocked task")
    print("✓ onboard includes active task\n")
    print("3b. agent-b should also see latest_handover after first handover")
    assert_true("latest_handover" in r4.brief, "onboard after handover should include latest_handover")

    print("4. ask: irrelevant should be 0 facts")
    r5 = brain_ask(BrainAskRequest(project_id=project_id, agent_id="agent-b", question="量子纠缠", limit=5), db_path=db_path)
    print(json.dumps(r5.model_dump(), ensure_ascii=False, indent=2))
    assert_eq(len(r5.facts), 0, "irrelevant should be 0 facts")
    assert_true(r5.match_mode == "none", "irrelevant match_mode none")
    print("✓ irrelevant filtered\n")

    print("5. ask: relevant should hit")
    r6 = brain_ask(BrainAskRequest(project_id=project_id, agent_id="agent-b", question="FTS 中文检索", limit=5), db_path=db_path)
    print(json.dumps(r6.model_dump(), ensure_ascii=False, indent=2))
    assert_true(len(r6.facts) >= 1, "relevant should hit")
    # answer should not be full state JSON dump
    assert_true(len(r6.answer) < 800 and "current_goal" not in r6.answer or r6.answer.count("{") < 3, "answer should be short extracted fields")
    print("✓ relevant hit with concise answer\n")

    print("6. scope filter")
    r7 = brain_ask(BrainAskRequest(project_id=project_id, agent_id="agent-b", question="MCP", scope=["search"], limit=5), db_path=db_path)
    print(json.dumps(r7.model_dump(), ensure_ascii=False, indent=2))
    assert_eq(len(r7.facts), 0, "MCP with scope search should be 0")
    print("✓ scope filter works\n")

    print("7. agent-b completes task")
    r8 = brain_handover(
        BrainHandoverRequest(project_id=project_id, agent_id="agent-b", session_id="session-13", task_id="T-001", status="completed", completed=["补充 scope 与无关查询回归"], remaining=[], recommended_next_step="验收通过"),
        db_path=db_path,
    )
    print(json.dumps(r8.model_dump(), ensure_ascii=False, indent=2))
    r9 = brain_onboard(BrainOnboardRequest(project_id=project_id, agent_id="agent-c", session_id="session-14"), db_path=db_path)
    print(json.dumps(r9.model_dump(), ensure_ascii=False, indent=2))
    assert_true(len(r9.brief.get("active_tasks", [])) == 0 and len(r9.brief.get("blocked_tasks", [])) == 0, "completed task should not appear as active")
    print("✓ completed task no longer active\n")

    print("=== Demo 完成 ===")


def main() -> None:
    import argparse

    p = argparse.ArgumentParser()
    p.add_argument("--db", help="db path (default: temp isolated db for reproducible demo)")
    p.add_argument("--project", default="project-brain")
    args = p.parse_args()

    if args.db:
        db_path = args.db
        run_demo(db_path, project_id=args.project)
    else:
        with tempfile.TemporaryDirectory() as tmp:
            tdir = Path(tmp) / "demo-proj"
            tdir.mkdir()
            (tdir / ".brain").mkdir()
            import json as _json

            (tdir / ".brain/config.json").write_text(_json.dumps({"project_id": args.project, "version": "0.2.0"}), encoding="utf-8")
            db_path = str(tdir / ".brain/brain.db")
            init_db(db_path)
            # seed same as init-brain --clean but inline for temp demo
            brain_record(
                BrainRecordRequest(
                    project_id=args.project,
                    agent_id="system",
                    session_id="init",
                    records=[
                        RecordInput(type="identity", content={"name": "Project Brain", "purpose": "跨 Agent 项目大脑"}, status="active", tags=["project-brain"]),
                        RecordInput(type="state", content={"current_goal": "完成 v0.2 可靠记忆方案", "phase": "v0.2", "blockers": [], "open_questions": [], "recent_changes": []}, status="active"),
                        RecordInput(type="task", content={"title": "完成 v0.2 可靠记忆方案", "remaining": ["验证"], "next_step": "验证"}, status="active", task_status="in_progress", tags=["task"]),
                        RecordInput(type="knowledge", content={"content": "MCP 兼容 mcp 1.x/2.x", "scope": "mcp"}, status="verified", tags=["mcp"]),
                        RecordInput(type="knowledge", content={"content": "检索为 FTS5 + bigram LIKE 回退 + 阈值", "scope": "search"}, status="verified", tags=["search"]),
                    ],
                ),
                db_path=db_path,
            )
            run_demo(db_path, project_id=args.project)
            print(f"\n(temp demo db was {db_path} — isolated, repeatable)")

    # also sanity: current repo brain still readable
    print("\n--- repo brain sanity ---")
    r = brain_onboard(BrainOnboardRequest(project_id="project-brain", agent_id="agent-c"))
    print(f"repo brain onboard source_ids={len(r.source_ids)}")


if __name__ == "__main__":
    main()
