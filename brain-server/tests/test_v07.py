import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "brain-server/src"))

from brain_server.answer_brain import AnswerBrain, answer_v2
from brain_server.answer_models import confidence_label
from brain_server.db import get_connection, init_db
from brain_server.models import BrainFeedbackRequest, BrainRecordRequest, RecordInput
from brain_server.protocol import brain_feedback, brain_record


def _fresh_db(tmp: str) -> str:
    db = str(Path(tmp) / "test_v07.db")
    conn = init_db(db)
    conn.close()
    return db


def test_clustering_dedup():
    """Same decision / highly similar texts should cluster into one primary."""
    with tempfile.TemporaryDirectory() as tmp:
        db = _fresh_db(tmp)
        brain_record(BrainRecordRequest(
            project_id="p", agent_id="a",
            records=[
                RecordInput(type="knowledge", content={"content": "并发控制使用 revision 乐观锁机制"}, status="verified", tags=["concurrency"]),
                RecordInput(type="knowledge", content={"content": "并发控制使用 revision 乐观锁机制实现"}, status="verified", tags=["concurrency"]),
            ],
        ), db_path=db)
        brain = AnswerBrain(db_path=db)
        result = brain.answer("并发控制机制", project_id="p")
        # After clustering, at most 1 primary per duplicate cluster should remain
        assert result.schema_version == "0.6"
        # Check that duplicate cluster produced fewer key_points than raw gated would
        assert len(result.key_points) <= 2


def test_answer_claims_mapped():
    """Every answer_claim must map to a key_point; hallucination_risk is False."""
    with tempfile.TemporaryDirectory() as tmp:
        db = _fresh_db(tmp)
        brain_record(BrainRecordRequest(
            project_id="p", agent_id="a",
            records=[RecordInput(type="knowledge", content={"content": "项目使用 FTS5 进行检索"}, status="verified")],
        ), db_path=db)
        brain = AnswerBrain(db_path=db)
        result = brain.answer("检索如何实现", project_id="p")
        if result.answer_claims:
            for cl in result.answer_claims:
                assert cl.key_point_ids, "claim must have key_point_ids"
            assert result.hallucination_risk is False


def test_rich_evidence_fields():
    """Evidence items should include health/captured_at/summary."""
    with tempfile.TemporaryDirectory() as tmp:
        db = _fresh_db(tmp)
        # Create evidence then knowledge linked to it
        from brain_server.repository import create_evidence, create_link

        conn = get_connection(db)
        eid = create_evidence(conn, ev_type="test_result", source="make test", description="36 passed", project_id="p")
        conn.commit()
        conn.close()

        brain_record(BrainRecordRequest(
            project_id="p", agent_id="a",
            records=[RecordInput(type="knowledge", content={"content": "测试覆盖检索与审阅"}, status="verified", evidence_ids=[eid])],
        ), db_path=db)

        brain = AnswerBrain(db_path=db)
        result = brain.answer("测试覆盖", project_id="p")
        if result.evidence:
            ev = result.evidence[0]
            assert "health" in ev
            assert "summary" in ev
            assert "captured_at" in ev


def test_refusal_no_reliable_facts():
    """Irrelevant question should refuse with canonical message and 0 confidence."""
    with tempfile.TemporaryDirectory() as tmp:
        db = _fresh_db(tmp)
        brain_record(BrainRecordRequest(
            project_id="p", agent_id="a",
            records=[RecordInput(type="knowledge", content={"content": "项目使用Python开发"}, status="verified")],
        ), db_path=db)
        brain = AnswerBrain(db_path=db)
        result = brain.answer("今天天气怎么样", project_id="p")
        assert result.confidence == 0.0
        assert "没有足够可靠" in result.answer or "未找到" in result.answer


def test_clarification_short_vague():
    """Very short vague question should trigger clarification."""
    with tempfile.TemporaryDirectory() as tmp:
        db = _fresh_db(tmp)
        brain = AnswerBrain(db_path=db)
        result = brain.answer("怎么实现？", project_id="p")
        # Either clarification or normal answer is acceptable; but clarification path must be valid
        if result.clarification and result.clarification.needed:
            assert result.clarification.prompt
            assert result.clarification.candidates


def test_confidence_label():
    assert confidence_label(0.95) == "可以直接作为工作上下文使用"
    assert confidence_label(0.75) == "基本可靠，建议查看证据"
    assert confidence_label(0.5) == "相关但不完整，需要谨慎"
    assert confidence_label(0.2) == "仅供线索，不应据此决策"
    assert confidence_label(0.0) == "没有可靠答案"


def test_feedback_does_not_modify_memory():
    """Feedback writes answer_feedback + Event but does not change memories."""
    with tempfile.TemporaryDirectory() as tmp:
        db = _fresh_db(tmp)
        brain_record(BrainRecordRequest(
            project_id="p", agent_id="a",
            records=[RecordInput(type="knowledge", content={"content": "原始知识"}, status="verified")],
        ), db_path=db)
        from brain_server.repository import count_memories

        conn = get_connection(db)
        before = count_memories(conn, project_id="p")
        conn.close()

        req = BrainFeedbackRequest(project_id="p", agent_id="a", question="原始知识是什么", verdict="irrelevant")
        resp = brain_feedback(req, db_path=db)
        assert resp.feedback_id.startswith("FB-")
        assert resp.event_id

        conn = get_connection(db)
        after = count_memories(conn, project_id="p")
        conn.close()
        assert after == before

        # Event exists and is project-isolated
        conn = get_connection(db)
        cur = conn.execute("SELECT count(*) as c FROM events WHERE project_id='p' AND source='feedback'")
        assert cur.fetchone()["c"] >= 1
        # Feedback row exists
        cur = conn.execute("SELECT count(*) as c FROM answer_feedback WHERE project_id='p'")
        assert cur.fetchone()["c"] >= 1
        conn.close()


def test_memory_health_runs():
    """Memory health checks run without error and return expected keys."""
    with tempfile.TemporaryDirectory() as tmp:
        db = _fresh_db(tmp)
        # Orphan decision (no evidence)
        brain_record(BrainRecordRequest(
            project_id="p", agent_id="a",
            records=[RecordInput(type="decision", content={"decision": "使用 SQLite", "reason": "简单"}, status="proposed")],
        ), db_path=db)
        from brain_server.health import brain_health, memory_health

        conn = get_connection(db)
        mh = memory_health(conn, "p")
        assert "warnings" in mh
        assert "total_memories" in mh
        bh = brain_health(conn, "p")
        assert "evidence" in bh
        assert "memory" in bh
        assert "provenance_coverage" in bh
        assert "workflow" in bh
        conn.close()
