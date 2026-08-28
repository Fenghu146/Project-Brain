import tempfile
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "brain-server/src"))

from brain_server.db import init_db
from brain_server.models import BrainAskRequest, BrainHandoverRequest, BrainOnboardRequest, BrainRecordRequest, RecordInput
from brain_server.protocol import brain_ask, brain_handover, brain_onboard, brain_record


def _fresh_db(tmp):
    db = str(Path(tmp) / "test.db")
    conn = init_db(db)
    conn.close()
    return db


def test_record_and_ask_and_onboard_and_handover():
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        db = _fresh_db(tmp)
        r = brain_record(BrainRecordRequest(project_id="p", agent_id="a", session_id="s1", records=[RecordInput(type="identity", content={"name": "Demo", "purpose": "test"}, status="active"), RecordInput(type="state", content={"current_goal": "验证链路", "open_questions": ["是否丢包"]}, status="active"), RecordInput(type="task", content={"title": "压测任务", "remaining": ["跑压测"], "next_step": "跑压测"}, status="active"), RecordInput(type="decision", content={"decision": "使用 circular DMA", "reason": "连续接收"}, tags=["dma"]), RecordInput(type="experience", content={"task": "修复丢包", "attempt": "normal DMA", "result": "failed", "lesson": "不适合连续"}, status="verified", tags=["dma"])]), db_path=db)
        assert len(r.accepted) >= 5
        assert any("Decision without evidence" in w for w in r.warnings)

        ob = brain_onboard(BrainOnboardRequest(project_id="p", agent_id="b", session_id="s2", focus="DMA"), db_path=db)
        assert "active_tasks" in ob.brief
        assert len(ob.source_ids) >= 3

        ask = brain_ask(BrainAskRequest(project_id="p", agent_id="b", question="normal DMA", limit=5), db_path=db)
        assert ask.facts

        h = brain_handover(BrainHandoverRequest(project_id="p", agent_id="a", session_id="s1", task_id=r.accepted[2], status="partial", completed=["完成压测"], remaining=["补充压测"], recommended_next_step="补充"), db_path=db)
        assert h.handover_id.startswith("H-")
