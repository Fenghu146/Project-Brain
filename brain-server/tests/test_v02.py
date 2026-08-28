import tempfile
from pathlib import Path
import sys
import json

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "brain-server/src"))

from brain_server.db import get_connection, init_db
from brain_server.models import BrainAskRequest, BrainHandoverRequest, BrainOnboardRequest, BrainRecordRequest, RecordInput
from brain_server.protocol import brain_ask, brain_handover, brain_onboard, brain_record


def test_project_isolation():
    with tempfile.TemporaryDirectory() as tmp:
        db = str(Path(tmp) / "b.db")
        brain_record(BrainRecordRequest(project_id="proj-a", agent_id="a", records=[RecordInput(type="knowledge", content={"content": "hello a secret uniqA"})]), db_path=db)
        brain_record(BrainRecordRequest(project_id="proj-b", agent_id="a", records=[RecordInput(type="knowledge", content={"content": "hello b other uniqB"})]), db_path=db)
        r = brain_ask(BrainAskRequest(project_id="proj-b", agent_id="a", question="hello a secret uniqA", limit=5), db_path=db)
        assert len(r.facts) == 0
        r2 = brain_ask(BrainAskRequest(project_id="proj-a", agent_id="a", question="hello a secret uniqA", limit=5), db_path=db)
        assert len(r2.facts) == 1


def test_irrelevant_zero_facts():
    with tempfile.TemporaryDirectory() as tmp:
        db = str(Path(tmp) / "b.db")
        brain_record(BrainRecordRequest(project_id="p", agent_id="a", records=[RecordInput(type="knowledge", content={"content": "hello world"})]), db_path=db)
        r = brain_ask(BrainAskRequest(project_id="p", agent_id="a", question="量子纠缠", limit=5), db_path=db)
        assert len(r.facts) == 0
        assert r.match_mode == "none"
        assert r.confidence == 0.0


def test_scope_filter():
    with tempfile.TemporaryDirectory() as tmp:
        db = str(Path(tmp) / "b.db")
        brain_record(BrainRecordRequest(project_id="p", agent_id="a", records=[RecordInput(type="knowledge", content={"content": "UART 接收由 DMA 驱动", "scope": "uart"}, tags=["uart"]), RecordInput(type="knowledge", content={"content": "支付回调幂等", "scope": "payment"}, tags=["payment"])]), db_path=db)
        r = brain_ask(BrainAskRequest(project_id="p", agent_id="a", question="DMA", scope=["uart"], limit=5), db_path=db)
        assert len(r.facts) == 1
        r2 = brain_ask(BrainAskRequest(project_id="p", agent_id="a", question="DMA", scope=["payment"], limit=5), db_path=db)
        assert len(r2.facts) == 0


def test_handover_task_missing_error():
    with tempfile.TemporaryDirectory() as tmp:
        db = str(Path(tmp) / "b.db")
        init_db(db)
        try:
            brain_handover(BrainHandoverRequest(project_id="p", agent_id="a", task_id="T-999", status="partial"), db_path=db)
            assert False, "should raise"
        except ValueError as e:
            assert "not found" in str(e)


def test_task_status_atomic_and_restart():
    with tempfile.TemporaryDirectory() as tmp:
        db = str(Path(tmp) / "b.db")
        brain_record(BrainRecordRequest(project_id="p", agent_id="a", records=[RecordInput(type="identity", content={"name": "P", "purpose": "x"}, status="active"), RecordInput(type="state", content={"current_goal": "v0.2"}, status="active"), RecordInput(type="task", content={"title": "v0.2", "remaining": ["验证"], "next_step": "验证"}, status="active", task_status="in_progress")]), db_path=db)
        brain_handover(BrainHandoverRequest(project_id="p", agent_id="a", task_id="T-001", status="partial", completed=["做完"], remaining=["剩"], recommended_next_step="下一步"), db_path=db)
        conn = get_connection(db)
        cur = conn.execute("SELECT task_status FROM memories WHERE id='T-001'")
        assert cur.fetchone()["task_status"] == "in_progress"
        conn.close()
        # restart: reopen and check
        conn2 = get_connection(db)
        assert conn2.execute("SELECT count(*) as c FROM handovers WHERE project_id='p'").fetchone()["c"] == 1
        conn2.close()
        r = brain_onboard(BrainOnboardRequest(project_id="p", agent_id="b"), db_path=db)
        assert "T-001" in str(r.brief)
        # complete -> no longer active
        brain_handover(BrainHandoverRequest(project_id="p", agent_id="b", task_id="T-001", status="completed", completed=["剩"]), db_path=db)
        r2 = brain_onboard(BrainOnboardRequest(project_id="p", agent_id="c"), db_path=db)
        assert len(r2.brief.get("active_tasks", [])) == 0


def test_answer_not_full_json_and_match_mode():
    with tempfile.TemporaryDirectory() as tmp:
        db = str(Path(tmp) / "b.db")
        brain_record(BrainRecordRequest(project_id="p", agent_id="a", records=[RecordInput(type="knowledge", content={"content": "MCP 兼容 mcp 1.x/2.x", "scope": "mcp"}, status="verified", tags=["mcp"])]), db_path=db)
        r = brain_ask(BrainAskRequest(project_id="p", agent_id="a", question="MCP 兼容", limit=5), db_path=db)
        assert len(r.facts) >= 1
        assert "current_goal" not in r.answer
        assert r.match_mode in ("fts", "like_fallback")


def test_duplicate_and_evidence_link():
    with tempfile.TemporaryDirectory() as tmp:
        db = str(Path(tmp) / "b.db")
        r = brain_record(BrainRecordRequest(project_id="p", agent_id="a", records=[RecordInput(type="decision", content={"decision": "使用 FTS5", "reason": "快速"}, tags=["search"], evidence=[{"type": "file", "source": "doc.md", "description": "d"}])]), db_path=db)
        mid = r.accepted[0]
        r2 = brain_record(BrainRecordRequest(project_id="p", agent_id="a", records=[RecordInput(type="decision", content={"decision": "使用 FTS5", "reason": "快速"}, tags=["search"])]), db_path=db)
        assert any("duplicate_of" in w for w in r2.warnings)
        conn = get_connection(db)
        links = list(conn.execute("SELECT * FROM links WHERE from_id=?", (mid,)).fetchall())
        assert any(dict(x)["relation"] == "evidence_of" for x in links)
        conn.close()
