#!/usr/bin/env python3
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "brain-server/src"))

from brain_server.db import init_db
from brain_server.models import BrainRecordRequest, RecordInput
from brain_server.protocol import brain_record

PROJECT_ID = "smart-gateway"


def main() -> None:
    db_path = ROOT / ".brain/brain.db"
    config_path = ROOT / ".brain/config.json"
    conn = init_db(str(db_path))
    conn.close()

    from datetime import datetime, timezone
    config = {"project_id": PROJECT_ID, "created_at": datetime.now(timezone.utc).isoformat(), "version": "0.1.0"}
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")

    seed = BrainRecordRequest(
        project_id=PROJECT_ID,
        agent_id="system",
        session_id="init",
        records=[
            RecordInput(type="identity", content={"name": "SmartGateway", "purpose": "工业数据采集网关", "architecture": ["STM32", "ESP32", "Linux Gateway"], "constraints": ["离线可运行", "低延迟", "资源受限"], "principles": ["可靠性优先", "显式状态优于隐式行为"]}, status="active", tags=["gateway", "identity"]),
            RecordInput(type="state", content={"current_goal": "完成 UART DMA 接收链路验证", "phase": "验证中", "blockers": ["硬件压力测试尚未完成"], "open_questions": ["高频接收时是否仍存在丢包"], "recent_changes": ["已完成 circular DMA 配置"], "recommended_next_step": "在目标硬件上运行连续帧压力测试"}, status="active", tags=["uart", "dma"]),
            RecordInput(type="task", content={"title": "完成 UART DMA 硬件压力测试", "status": "in_progress", "completed": ["配置 circular DMA", "增加 IDLE 中断处理"], "remaining": ["运行硬件压力测试", "记录丢包率"], "next_step": "在目标硬件上运行连续帧测试"}, status="active", tags=["uart", "dma", "task"]),
            RecordInput(type="decision", content={"decision": "UART 接收使用 circular DMA", "reason": "满足连续接收需求，并避开 normal DMA 在高频场景下的已知问题", "alternatives_considered": ["normal DMA", "纯轮询"]}, status="proposed", tags=["uart", "dma", "decision"], evidence=[{"type": "test_result", "source": "tests/dma_test.log", "description": "高频接收压力测试结果"}]),
            RecordInput(type="experience", content={"task": "修复 UART 高频接收丢包", "attempt": "将 DMA 从 circular mode 改为 normal mode", "result": "failed", "reason": "连续接收场景下出现数据丢失", "conditions": ["高频输入", "连续帧"], "lesson": "normal DMA 不适合持续接收场景"}, status="verified", tags=["uart", "dma", "failure"], evidence=[{"type": "test_result", "source": "tests/dma_test.log", "description": "normal DMA 失败测试日志"}]),
            RecordInput(type="knowledge", content={"content": "UART 接收由 DMA 缓冲区和 IDLE 中断共同驱动。", "scope": "uart"}, status="verified", tags=["uart", "knowledge"]),
        ],
    )
    resp = brain_record(seed, db_path=str(db_path))
    print(f"Seed done: accepted={resp.accepted}")
    if resp.warnings:
        print(f"Warnings: {resp.warnings}")
    print(f"DB: {db_path}")
    print(f"Config: {config_path}")


if __name__ == "__main__":
    main()
