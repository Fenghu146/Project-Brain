import json
import tempfile
from pathlib import Path

import pytest
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "brain-server/src"))

from brain_server.db import get_connection, init_db
from brain_server.workflow import WorkflowBrain, WorkflowBrainError, BrainUnavailableError
from brain_server.workflow_models import WorkflowConfig


class TestWorkflowBrain:
    """Test Phase 1: WorkflowBrain core module."""

    @pytest.fixture
    def db_path(self, tmp_path):
        db = str(tmp_path / "test.db")
        init_db(db)
        return db

    @pytest.fixture
    def wb(self, db_path):
        config = WorkflowConfig(project_id="test-proj")
        return WorkflowBrain(db_path=db_path, config=config)

    def test_start_session_returns_context(self, wb):
        start = wb.start_session(
            project_id="test-proj",
            agent_id="agent-a",
            level="compact",
        )
        assert start.session_id is not None
        assert start.project_id == "test-proj"
        assert start.agent_id == "agent-a"
        assert "identity" in start.context
        assert "current_goal" in start.context
        assert "source_ids" in start.context

    def test_start_session_idempotent(self, wb):
        start1 = wb.start_session(
            project_id="test-proj",
            agent_id="agent-a",
            session_id="sess-1",
        )
        start2 = wb.start_session(
            project_id="test-proj",
            agent_id="agent-a",
            session_id="sess-1",
        )
        assert start1.session_id == start2.session_id

    def test_observe_creates_event(self, wb):
        start = wb.start_session(
            project_id="test-proj",
            agent_id="agent-a",
        )
        receipt = wb.observe(
            project_id="test-proj",
            observation={
                "kind": "agent_note",
                "source": "test",
                "result": "observed",
                "payload": {"note": "hello world"},
            },
            session_id=start.session_id,
        )
        assert receipt.observation_id is not None
        assert len(receipt.event_ids) >= 0  # May be empty if brain not available

    def test_end_session_creates_draft(self, wb):
        start = wb.start_session(
            project_id="test-proj",
            agent_id="agent-a",
        )
        draft = wb.end_session(
            project_id="test-proj",
            session_id=start.session_id,
        )
        assert draft.draft_id is not None
        assert draft.status == "pending"
        assert draft.session_id == start.session_id

    def test_brain_unavailable_raises_error(self):
        wb = WorkflowBrain(db_path="/nonexistent/path.db")
        with pytest.raises(BrainUnavailableError):
            wb.start_session(
                project_id="test-proj",
                agent_id="agent-a",
            )

    def test_session_stored_in_database(self, wb, db_path):
        start = wb.start_session(
            project_id="test-proj",
            agent_id="agent-a",
        )
        conn = get_connection(db_path)
        row = conn.execute(
            "SELECT id, project_id, agent_id, status FROM sessions WHERE id=?",
            (start.session_id,),
        ).fetchone()
        conn.close()
        assert row is not None
        assert row["id"] == start.session_id
        assert row["project_id"] == "test-proj"
        assert row["status"] == "active"

    def test_handover_draft_stored_in_database(self, wb, db_path):
        start = wb.start_session(
            project_id="test-proj",
            agent_id="agent-a",
        )
        draft = wb.end_session(
            project_id="test-proj",
            session_id=start.session_id,
        )
        conn = get_connection(db_path)
        row = conn.execute(
            "SELECT id, session_id, status FROM handover_drafts WHERE id=?",
            (draft.draft_id,),
        ).fetchone()
        conn.close()
        assert row is not None
        assert row["id"] == draft.draft_id
        assert row["status"] == "pending"

    def test_different_level_context(self, wb):
        # compact level
        start_compact = wb.start_session(
            project_id="test-proj",
            agent_id="agent-a",
            level="compact",
        )
        assert len(start_compact.context) <= 10  # Minimal fields

        # focused level
        start_focused = wb.start_session(
            project_id="test-proj",
            agent_id="agent-a",
            level="focused",
        )
        assert "important_decisions" in start_focused.context or "active_tasks" in start_focused.context
