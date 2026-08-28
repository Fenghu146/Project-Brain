#!/usr/bin/env python3
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "brain-server/src"))

from brain_server.db import init_db
from brain_server.models import BrainRecordRequest, RecordInput
from brain_server.protocol import brain_record

PROJECT_ID = "project-brain"


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default=PROJECT_ID)
    ap.add_argument("--clean", action="store_true")
    args2 = ap.parse_args()
    project_id = args2.project
    db_path = ROOT / ".brain/brain.db"
    config_path = ROOT / ".brain/config.json"
    if args2.clean and db_path.exists():
        db_path.unlink()
        for suf in [".db-journal", ".db-wal", ".db-shm"]:
            pf = ROOT / f".brain/brain.db{suf}"
            if pf.exists():
                pf.unlink()
            pf2 = Path(str(db_path) + suf)
            if pf2.exists():
                pf2.unlink()
    conn = init_db(str(db_path))
    conn.close()

    from datetime import datetime, timezone
    config = {"project_id": project_id, "created_at": datetime.now(timezone.utc).isoformat(), "version": "0.2.0"}
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")

    _seed = BrainRecordRequest(
        project_id=project_id,
        agent_id="system",
        session_id="init",
        records=[
            RecordInput(type="identity", content={"name": "Project Brain", "purpose": "跨 Agent 项目大脑：让项目持续拥有身份、状态、知识、经验、决策与证据，换 Agent 不换脑", "architecture": ["Python + SQLite + FTS5", "MCP Server", "规则 Curator"], "constraints": ["可多项目同库隔离", "零向量库"], "principles": ["证据优先", "失败经验不删", "冲突不覆盖"]}, status="active", tags=["project-brain", "identity"]),
            RecordInput(type="state", content={"current_goal": "完成 v0.2 可靠记忆方案", "phase": "v0.2", "blockers": [], "open_questions": ["是否接入本地小模型 Curator"], "recent_changes": ["交付 v0.1-mvp", "接入 CLI 跨项目能力"], "recommended_next_step": "验证多项目隔离与交接"}, status="active", tags=["project-brain"]),
            RecordInput(type="task", content={"title": "完成 v0.2 可靠记忆方案", "remaining": ["验证隔离与检索阈值", "验证任务状态流转"], "next_step": "运行隔离与交接验证"}, status="active", task_status="in_progress", tags=["project-brain", "task"]),
            RecordInput(type="knowledge", content={"content": "MCP 兼容 mcp 1.x/2.x，工具名 brain_onboard/brain_ask/brain_record/brain_handover/brain_verify/brain_link，底层由 protocol.py 统一编排。", "scope": "mcp"}, status="verified", tags=["mcp", "knowledge"]),
            RecordInput(type="knowledge", content={"content": "检索为 FTS5 + 中文 bigram LIKE 回退 + 相关度阈值，未命中不凑答案；支持 scope 过滤与 match_mode/matched_terms。", "scope": "search"}, status="verified", tags=["search", "knowledge"]),
            RecordInput(type="decision", content={"decision": "v0.2 仅用 SQLite FTS5，不引入向量库", "reason": "先保证跨项目隔离与检索可信度，通过验收后再考虑 embedding", "alternatives_considered": ["引入向量库"]}, status="active", tags=["decision", "search"], evidence=[{"type": "file", "source": "project-brain-v0.2.md", "description": "v0.2 目标与非目标"}]),
            RecordInput(type="experience", content={"task": "FTS 中文检索", "attempt": "仅 FTS MATCH", "result": "failed", "reason": "中文未分词导致无命中", "conditions": ["中文长句"], "lesson": "增加 LIKE bigram 回退并加阈值过滤"}, status="verified", tags=["search", "failure"], evidence=[{"type": "file", "source": "brain-server/src/brain_server/repository.py", "description": "fts_search 回退"}]),
        ],
    )
    resp = brain_record(_seed, db_path=str(db_path))
    print(f"Seed done: project={project_id} accepted={resp.accepted}")
    if resp.warnings:
        print(f"Warnings: {resp.warnings}")
    print(f"DB: {db_path}")
    print(f"Config: {config_path}")


if __name__ == "__main__":
    main()
