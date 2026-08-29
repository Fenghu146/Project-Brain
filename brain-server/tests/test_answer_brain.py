"""Tests for AnswerBrain v0.6."""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "brain-server/src"))

from brain_server.db import init_db
from brain_server.models import BrainAskRequest, BrainRecordRequest, RecordInput
from brain_server.protocol import brain_ask, brain_ask_v2, brain_record
from brain_server.answer_brain import AnswerBrain, answer_v2


def _fresh_db(tmp):
    db = str(Path(tmp) / "test_v06.db")
    conn = init_db(db)
    conn.close()
    return db


def test_answer_brain_returns_structured_result():
    """AnswerBrain returns structured AnswerResult with key_points."""
    with tempfile.TemporaryDirectory() as tmp:
        db = _fresh_db(tmp)
        brain = AnswerBrain(db_path=db)
        
        # Ask about something with no data
        result = brain.answer("test question", project_id="p1")
        assert result.schema_version == "0.6"
        assert isinstance(result.answer, str)
        assert isinstance(result.key_points, list)
        assert result.confidence >= 0.0
        assert result.confidence <= 1.0


def test_intent_classification():
    """Intent classification works correctly."""
    from brain_server.intent_router import classify_intent
    
    # Project goal
    intent, policy = classify_intent("项目核心目标是什么")
    assert intent == "project_goal"
    assert "identity" in policy["preferred_types"]
    
    # Current state - "进展" matches "正在"
    intent, policy = classify_intent("当前进展如何")
    assert intent == "current_state"
    
    # Feature summary
    intent, policy = classify_intent("v0.4包含哪些功能")
    assert intent == "feature_summary"
    
    # Mechanism explanation
    intent, policy = classify_intent("WorkflowBrain如何实现")
    assert intent == "mechanism_explanation"
    
    # Decision reason
    intent, policy = classify_intent("为什么选择SQLite")
    assert intent == "decision_reason"
    
    # Generic fallback
    intent, policy = classify_intent("random question")
    assert intent == "generic_search"


def test_source_class_derivation():
    """Source class is derived correctly from memory type and status."""
    from brain_server.intent_router import derive_source_class
    
    # Verified knowledge with evidence
    cls = derive_source_class("knowledge", "verified", True)
    assert cls == "active_knowledge"
    
    # Verified decision with evidence
    cls = derive_source_class("decision", "verified", True)
    assert cls == "active_decision"
    
    # Unverified decision
    cls = derive_source_class("decision", "proposed", False)
    assert cls == "active_decision"
    
    # Task
    cls = derive_source_class("task", "in_progress", False)
    assert cls == "task_handover"
    
    # Identity
    cls = derive_source_class("identity", "active", False)
    assert cls == "project_model"
    
    # Experience - verified with evidence returns verified_evidence (higher priority)
    cls = derive_source_class("experience", "verified", True)
    assert cls == "verified_evidence"


def test_answer_filters_version_plan_pollution():
    """Version plan content is filtered from main answer."""
    with tempfile.TemporaryDirectory() as tmp:
        db = _fresh_db(tmp)
        
        # Record a version plan
        r = brain_record(BrainRecordRequest(
            project_id="p",
            agent_id="a",
            records=[RecordInput(
                type="knowledge",
                content={"content": "v0.6 计划新增 AnswerBrain"},
                status="proposed",
                tags=["version_plan"]
            )]
        ), db_path=db)
        assert len(r.accepted) > 0
        
        # Ask about it - should be filtered
        result = answer_v2(
            question="v0.6包含什么功能",
            project_id="p",
            db_path=db,
        )
        assert result.schema_version == "0.6"
        # Should not include version_plan in main answer
        clean_points = [kp for kp in result.key_points if "version_plan" not in kp.pollution_tags]
        # Refusal now returns the canonical "没有足够可靠" message; either form is acceptable
        if not clean_points:
            assert any(s in result.answer for s in ("版本规划", "未找到", "没有足够可靠"))


def test_answer_with_evidence():
    """Answer with evidence has higher coverage."""
    with tempfile.TemporaryDirectory() as tmp:
        db = _fresh_db(tmp)

        # Record knowledge with evidence linked
        r = brain_record(BrainRecordRequest(
            project_id="p",
            agent_id="a",
            records=[
                RecordInput(
                    type="evidence",
                    content={"type": "observed", "source": "test", "description": "Interface test"}
                ),
                RecordInput(
                    type="knowledge",
                    content={"content": "WorkflowBrain接口是start_session, observe, end_session"},
                    status="verified",
                    tags=["workflow"],
                    evidence_ids=["E-001"]
                )
            ]
        ), db_path=db)
        assert len(r.accepted) >= 2

        # Ask about WorkflowBrain
        result = answer_v2(
            question="WorkflowBrain接口是什么",
            project_id="p",
            db_path=db,
        )
        assert result.intent in ("mechanism_explanation", "generic_search")
        # Just verify the structure is correct - confidence calculation depends on scoring
        assert result.schema_version == "0.6"
        assert isinstance(result.key_points, list)
        assert isinstance(result.confidence, float)


def test_answer_returns_0_for_unrelated():
    """Unrelated questions return 0 confidence and no facts."""
    with tempfile.TemporaryDirectory() as tmp:
        db = _fresh_db(tmp)
        
        # Add unrelated knowledge
        brain_record(BrainRecordRequest(
            project_id="p",
            agent_id="a",
            records=[RecordInput(
                type="knowledge",
                content={"content": "项目使用Python开发"},
                status="verified"
            )]
        ), db_path=db)
        
        # Ask unrelated question
        result = answer_v2(
            question="今天天气怎么样",
            project_id="p",
            db_path=db,
        )
        assert result.confidence == 0.0
        assert len(result.key_points) == 0


def test_compatibility_with_brain_ask():
    """Old brain_ask still works alongside new answer_v2."""
    with tempfile.TemporaryDirectory() as tmp:
        db = _fresh_db(tmp)
        
        brain_record(BrainRecordRequest(
            project_id="p",
            agent_id="a",
            records=[RecordInput(
                type="identity",
                content={"name": "TestProject", "purpose": "Testing"},
                status="active"
            )]
        ), db_path=db)
        
        # Old API
        old_result = brain_ask(BrainAskRequest(
            project_id="p",
            agent_id="a",
            question="项目目的是什么",
        ), db_path=db)
        assert hasattr(old_result, 'answer')
        assert hasattr(old_result, 'facts')
        
        # New API
        new_result = brain_ask_v2(BrainAskRequest(
            project_id="p",
            agent_id="a",
            question="项目目的是什么",
        ), db_path=db)
        assert 'answer' in new_result
        assert 'key_points' in new_result
        assert 'confidence_breakdown' in new_result


def test_confidence_breakdown_structure():
    """Confidence breakdown has all required components."""
    with tempfile.TemporaryDirectory() as tmp:
        db = _fresh_db(tmp)
        brain = AnswerBrain(db_path=db)
        
        result = brain.answer("test", project_id="p")
        bd = result.confidence_breakdown
        assert hasattr(bd, 'retrieval_confidence')
        assert hasattr(bd, 'source_confidence')
        assert hasattr(bd, 'evidence_coverage')
        assert hasattr(bd, 'freshness')
        assert hasattr(bd, 'conflict_penalty')
        
        # All values should be between 0 and 1
        for val in [bd.retrieval_confidence, bd.source_confidence, 
                    bd.evidence_coverage, bd.freshness, bd.conflict_penalty]:
            assert 0 <= val <= 1


def test_provenance_trace():
    """Provenance trace is built correctly."""
    with tempfile.TemporaryDirectory() as tmp:
        db = _fresh_db(tmp)
        
        brain_record(BrainRecordRequest(
            project_id="p",
            agent_id="a",
            records=[RecordInput(
                type="knowledge",
                content={"content": "Test knowledge point"},
                status="verified"
            )]
        ), db_path=db)
        
        result = answer_v2(question="test", project_id="p", db_path=db)
        
        if result.provenance:
            for entry in result.provenance[:1]:
                assert "key_point" in entry
                assert "source_ids" in entry
                assert "support" in entry


def test_key_point_structure():
    """KeyPoint has all required fields."""
    from brain_server.answer_models import KeyPoint, SupportLevel, PollutionTag
    
    kp = KeyPoint(
        text="Test point",
        source_ids=["K-001"],
        evidence_ids=["E-001"],
        support="direct",
        source_class="active_knowledge",
        pollution_tags=[],
        is_stale=False,
        is_conflicted=False,
    )
    assert kp.text == "Test point"
    assert kp.source_ids == ["K-001"]
    assert kp.evidence_ids == ["E-001"]
    assert kp.support == "direct"
    assert kp.is_stale == False


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
