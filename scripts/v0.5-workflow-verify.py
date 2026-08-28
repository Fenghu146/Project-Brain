import tempfile
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "brain-server/src"))

from brain_server.db import init_db
from brain_server.workflow import WorkflowBrain, BrainUnavailableError
from brain_server.workflow_models import WorkflowConfig


def ok(name, cond, detail=""):
    print(f"{'✓' if cond else '✗ FAIL'} {name}" + (f" — {detail}" if detail else ""))


def test_phase1_core():
    """Test Phase 1: Core WorkflowBrain module."""
    with tempfile.TemporaryDirectory() as tmp:
        db = str(Path(tmp) / "test.db")
        init_db(db)
        wb = WorkflowBrain(db_path=db, config=WorkflowConfig(project_id="test-proj"))

        # start_session
        start = wb.start_session(project_id="test-proj", agent_id="agent-a", level="compact")
        assert start.session_id is not None
        assert "identity" in start.context
        print("✓ Phase 1: start_session returns context")

        # idempotent start
        start2 = wb.start_session(project_id="test-proj", agent_id="agent-a", session_id=start.session_id)
        assert start2.session_id == start.session_id
        print("✓ Phase 1: start_session idempotent")

        # observe
        receipt = wb.observe(
            project_id="test-proj",
            observation={"kind": "agent_note", "source": "test", "payload": {"note": "hello"}},
            session_id=start.session_id,
        )
        assert receipt.observation_id is not None
        print("✓ Phase 1: observe creates receipt")

        # end_session
        draft = wb.end_session(project_id="test-proj", session_id=start.session_id)
        assert draft.draft_id is not None
        assert draft.status == "pending"
        print("✓ Phase 1: end_session creates draft")

        # brain unavailable
        wb_bad = WorkflowBrain(db_path="/nonexistent")
        try:
            wb_bad.start_session(project_id="test-proj", agent_id="a")
            assert False, "should raise"
        except BrainUnavailableError:
            print("✓ Phase 1: BrainUnavailableError raised")


def test_phase2_buffer():
    """Test Phase 2: Buffer and privacy."""
    from brain_server.workflow_buffer import SessionBuffer, PrivacyFilter, Observation

    buf = SessionBuffer(max_items=10, ttl_minutes=30)
    obs = Observation(kind="test", source="make test", result="passed", payload={})
    buf.add("proj", "sess", obs)
    assert len(buf.get_pending("proj", "sess")) == 1
    print("✓ Phase 2: SessionBuffer works")

    privacy = PrivacyFilter(exclude_paths=["secrets", ".env"], redact_patterns=[r"(?i)password"])
    assert not privacy.should_skip(obs)
    # Test with excluded path
    secret_obs = Observation(kind="agent_note", source="/path/to/secrets/.env", payload={})
    assert privacy.should_skip(secret_obs)
    # Test with redact pattern
    pwd_obs = Observation(kind="agent_note", source="test", payload={"password": "secret123"})
    assert privacy.should_skip(pwd_obs)
    print("✓ Phase 2: PrivacyFilter blocks sensitive data")


def test_phase3_cli():
    """Test Phase 3: CLI commands."""
    import subprocess
    with tempfile.TemporaryDirectory() as tmp:
        db = str(Path(tmp) / "cli.db")
        init_db(db)
        rc = subprocess.run(
            ["python3", "-m", "brain_server.cli", "--db", db, "workflow", "--project", "p", "--action", "status"],
            capture_output=True, text=True
        )
        assert rc.returncode == 0, rc.stderr
        print("✓ Phase 3: workflow status CLI works")


def test_all():
    test_phase1_core()
    test_phase2_buffer()
    test_phase3_cli()
    print("\n=== All v0.5 workflow tests passed ===")


if __name__ == "__main__":
    test_all()
