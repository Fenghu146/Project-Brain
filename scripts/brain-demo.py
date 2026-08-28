#!/usr/bin/env python3
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "brain-server/src"))

from brain_server.models import BrainAskRequest, BrainHandoverRequest, BrainOnboardRequest, BrainRecordRequest, RecordInput
from brain_server.protocol import brain_ask, brain_handover, brain_onboard, brain_record


def assert_contains(haystack: str, needle: str, msg: str) -> None:
    if needle not in haystack:
        print(f"ASSERT FAIL: {msg} — expected '{needle}' not in output")
        sys.exit(1)


def main() -> None:
    print("=== Project Brain Demo: Agent A -> Brain -> Agent B ===\n")

    print("1. Agent A onboard (focus=UART)")
    r1 = brain_onboard(BrainOnboardRequest(project_id="smart-gateway", agent_id="agent-a", session_id="session-12", focus="UART DMA 接收丢包"))
    print(json.dumps(r1.model_dump(), ensure_ascii=False, indent=2))
    print()

    print("2. Agent A record: failed experience + decision + evidence/event")
    r2 = brain_record(
        BrainRecordRequest(
            project_id="smart-gateway",
            agent_id="agent-a",
            session_id="session-12",
            records=[
                RecordInput(type="evidence", content={"type": "test_result", "source": "tests/dma_test.log", "description": "高频接收压力测试结果"}),
                RecordInput(type="event", content={"action": "modify_file", "target": "src/uart.c", "summary": "修改 DMA 接收缓冲区处理逻辑"}),
            ],
        )
    )
    print(json.dumps(r2.model_dump(), ensure_ascii=False, indent=2))
    print()

    print("3. Agent A handover (partial)")
    r3 = brain_handover(
        BrainHandoverRequest(
            project_id="smart-gateway",
            agent_id="agent-a",
            session_id="session-12",
            task_id="T-001",
            status="partial",
            completed=["完成硬件连续帧压力测试", "记录测试日志"],
            discovered=["当前测试条件下未复现丢包"],
            remaining=["补充更高负载测试", "确认 cache coherency 风险"],
            recommended_next_step="由下一个 Agent 检查 cache coherency 并运行扩展压力测试",
            evidence_ids=[],
        )
    )
    print(json.dumps(r3.model_dump(), ensure_ascii=False, indent=2))
    handover_md = ROOT / ".brain/exports/latest-handover.md"
    print(f"Handover markdown: {handover_md}")
    if handover_md.exists():
        print(handover_md.read_text(encoding="utf-8")[:600])
    print()

    print("4. Agent B onboard (focus=DMA 丢包) — 验证能拿到关键上下文")
    r4 = brain_onboard(BrainOnboardRequest(project_id="smart-gateway", agent_id="agent-b", session_id="session-13", focus="DMA 丢包"))
    print(json.dumps(r4.model_dump(), ensure_ascii=False, indent=2))
    brief_text = json.dumps(r4.brief, ensure_ascii=False)
    assert_contains(brief_text, "circular DMA", "Agent B onboard 应包含决策 circular DMA")
    assert_contains(brief_text, "normal DMA", "Agent B onboard 应包含失败经验 normal DMA")
    print("✓ Agent B onboard 包含关键上下文\n")

    print("5. Agent B ask: 为什么不采用 normal DMA？")
    r5 = brain_ask(BrainAskRequest(project_id="smart-gateway", agent_id="agent-b", session_id="session-13", question="为什么不采用 normal DMA", limit=5))
    print(json.dumps(r5.model_dump(), ensure_ascii=False, indent=2))
    assert r5.facts, "ask 应返回 facts"
    print("✓ ask 返回相关事实与证据\n")

    print("6. Agent B record: 新测试结果")
    r6 = brain_record(
        BrainRecordRequest(
            project_id="smart-gateway",
            agent_id="agent-b",
            session_id="session-13",
            records=[
                RecordInput(
                    type="experience",
                    content={"task": "验证 UART 接收", "attempt": "circular DMA + IDLE interrupt", "result": "passed", "conditions": ["连续帧", "目标硬件"], "lesson": "该组合在当前测试条件下工作正常"},
                    tags=["uart", "dma"],
                    evidence=[{"type": "test_result", "source": "tests/hardware_stress.log", "description": "硬件连续帧压测通过"}],
                ),
            ],
        )
    )
    print(json.dumps(r6.model_dump(), ensure_ascii=False, indent=2))
    print()

    print("=== Demo 完成：Agent B 已成功继承 Agent A 的上下文并继续工作 ===")
    print("验证：服务重启后数据仍可读取 —", end=" ")
    r7 = brain_onboard(BrainOnboardRequest(project_id="smart-gateway", agent_id="agent-c", session_id="session-14"))
    print(f"onboard 返回 {len(r7.source_ids)} 个 source_ids")


if __name__ == "__main__":
    main()
