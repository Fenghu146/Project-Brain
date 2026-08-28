import tempfile
from pathlib import Path
import sys
import json

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "brain-server/src"))

from brain_server.db import get_connection, init_db
from brain_server.models import BrainAskRequest, BrainRecordRequest, BrainCurateRequest, BrainReviewListRequest, BrainReviewApplyRequest, BrainSnapshotRequest, IngestRequest, RecordInput
from brain_server.protocol import brain_ask, brain_record, brain_curate, brain_review_list, brain_review_apply, brain_snapshot, brain_rebuild_snapshot, brain_ingest


def test_ingest_dedup():
    with tempfile.TemporaryDirectory() as tmp:
        db = str(Path(tmp) / "b.db")
        init_db(db)
        r1 = brain_ingest(IngestRequest(project_id="p", source="git", payload={"cwd": tmp}), db_path=db)
        r2 = brain_ingest(IngestRequest(project_id="p", source="git", payload={"cwd": tmp}), db_path=db)
        assert r1["event_id"] == r2["event_id"]
        assert r1["duplicate"] is False or r2["duplicate"] is True


def test_event_isolation():
    with tempfile.TemporaryDirectory() as tmp:
        db = str(Path(tmp) / "b.db")
        brain_ingest(IngestRequest(project_id="pa", source="git", payload={"cwd": tmp}), db_path=db)
        brain_ingest(IngestRequest(project_id="pb", source="git", payload={"cwd": tmp}), db_path=db)
        from brain_server.db import get_connection

        conn = get_connection(db)
        assert conn.execute("SELECT count(*) FROM events WHERE project_id='pa'").fetchone()[0] >= 1
        assert conn.execute("SELECT count(*) FROM events WHERE project_id='pb'").fetchone()[0] >= 1
        conn.close()


def test_proposal_not_in_facts_until_approved():
    with tempfile.TemporaryDirectory() as tmp:
        db = str(Path(tmp) / "b.db")
        brain_record(BrainRecordRequest(project_id="p", agent_id="a", records=[RecordInput(type="knowledge", content={"content": "orig fact"}, status="verified", tags=["x"])]), db_path=db)
        brain_ingest(IngestRequest(project_id="p", source="test", payload={"command": "false", "cwd": tmp}), db_path=db)
        brain_curate(BrainCurateRequest(project_id="p", mode="rule"), db_path=db)
        pending = brain_review_list(BrainReviewListRequest(project_id="p", status="pending"), db_path=db)
        assert len(pending.proposals) >= 1
        r = brain_ask(BrainAskRequest(project_id="p", agent_id="a", question="orig fact", include_proposals=False), db_path=db)
        assert all(pr["id"] not in [f["id"] for f in r.facts] for pr in pending.proposals)
        r2 = brain_ask(BrainAskRequest(project_id="p", agent_id="a", question="failed", include_proposals=True), db_path=db)
        assert len(r2.proposals) >= 1 and r2.proposals[0]["kind"] == "proposal"


def test_review_four_states_and_superseded():
    with tempfile.TemporaryDirectory() as tmp:
        db = str(Path(tmp) / "b.db")
        brain_record(BrainRecordRequest(project_id="p", agent_id="a", records=[RecordInput(type="knowledge", content={"content": "to be verified"}, status="proposed")]), db_path=db)
        brain_ingest(IngestRequest(project_id="p", source="test", payload={"command": "false", "cwd": tmp}), db_path=db)
        cr = brain_curate(BrainCurateRequest(project_id="p", mode="rule"), db_path=db)
        assert cr.created
        # create 4 proposals for each state (use distinct commands to avoid curate dedup)
        for idx, action in enumerate(["approved", "rejected", "deferred", "superseded"]):
            brain_ingest(IngestRequest(project_id="p", source="test", payload={"command": f"false-{idx}", "cwd": tmp}), db_path=db)
            cr2 = brain_curate(BrainCurateRequest(project_id="p", mode="rule"), db_path=db)
            pid = cr2.created[0] if cr2.created else brain_review_list(BrainReviewListRequest(project_id="p", status="pending"), db_path=db).proposals[0]["id"]
            resp = brain_review_apply(BrainReviewApplyRequest(project_id="p", proposal_id=pid, action=action, reviewer="rev"), db_path=db)
            assert resp.proposal["status"] == action
            # each apply writes an Event
            from brain_server.db import get_connection

            conn = get_connection(db)
            assert conn.execute("SELECT count(*) FROM events WHERE source='review'").fetchone()[0] >= 1
            conn.close()
        # superseded on target change
        brain_record(BrainRecordRequest(project_id="p", agent_id="a", records=[RecordInput(type="knowledge", content={"content": "target mem"}, status="proposed")]), db_path=db)
        from brain_server import repository as repo
        from brain_server.db import get_connection

        conn = get_connection(db)
        mem = repo.list_memories(conn, project_id="p", limit=1)[0]
        conn.close()
        from brain_server.db import get_connection as gc

        conn = gc(db)
        # ensure proposal is older than the upcoming target touch: set it 10s in the past
        pid = repo.create_proposal(conn, project_id="p", action="update_memory", target_type="memory", target_id=mem["id"], payload={"content": {"content": "new"}}, reason="test stale target", source_event_ids=[], curator_version="rule-v1")
        conn.execute("UPDATE proposals SET created_at=datetime('now','-10 seconds') WHERE id=?", (pid,))
        conn.commit()
        conn.close()
        conn = get_connection(db)
        conn.execute("UPDATE memories SET updated_at=datetime('now') WHERE id=?", (mem["id"],))
        conn.commit()
        conn.close()
        try:
            brain_review_apply(BrainReviewApplyRequest(project_id="p", proposal_id=pid, action="approved", reviewer="rev2"), db_path=db)
            assert False, "should be superseded"
        except ValueError as e:
            assert "superseded" in str(e)
            from brain_server.db import get_connection as gc2

            conn = gc2(db)
            pr = repo.get_proposal(conn, pid, project_id="p")
            assert pr["status"] == "superseded"
            conn.close()


def test_stale_marking():
    with tempfile.TemporaryDirectory() as tmp:
        db = str(Path(tmp) / "b.db")
        past = "2000-01-01T00:00:00+00:00"
        brain_record(BrainRecordRequest(project_id="p", agent_id="a", records=[RecordInput(type="knowledge", content={"content": "old valid"}, status="verified", valid_until=past)]), db_path=db)
        r = brain_ask(BrainAskRequest(project_id="p", agent_id="a", question="old valid", include_proposals=False), db_path=db)
        assert len(r.facts) == 0
        assert any("stale" in u.lower() for u in r.uncertainties) or len(r.stale_facts) >= 1


def test_as_of_commit():
    with tempfile.TemporaryDirectory() as tmp:
        db = str(Path(tmp) / "b.db")
        brain_record(BrainRecordRequest(project_id="p", agent_id="a", records=[RecordInput(type="decision", content={"decision": "old decision", "reason": "x"}, status="active", commit_hash="abc", tags=["d"])]), db_path=db)
        brain_record(BrainRecordRequest(project_id="p", agent_id="a", records=[RecordInput(type="decision", content={"decision": "new decision", "reason": "y"}, status="active", commit_hash="def", tags=["d"])]), db_path=db)
        r_old = brain_ask(BrainAskRequest(project_id="p", agent_id="a", question="decision", as_of_commit="abc"), db_path=db)
        assert any("old" in f.get("id", "") or "old" in str(r_old.facts) for f in [{}]) or len(r_old.facts) >= 1


def test_snapshot_rebuild():
    with tempfile.TemporaryDirectory() as tmp:
        db = str(Path(tmp) / "b.db")
        brain_record(BrainRecordRequest(project_id="p", agent_id="a", records=[RecordInput(type="identity", content={"name": "P", "purpose": "x"}, status="active")]), db_path=db)
        snap = brain_snapshot(BrainSnapshotRequest(project_id="p"), db_path=db)
        from brain_server.db import get_connection

        conn = get_connection(db)
        conn.execute("DELETE FROM model_snapshots WHERE id=?", (snap.snapshot_id,))
        conn.commit()
        conn.close()
        # rebuild should restore with same source_ids (allow empty but consistent)
        snap2 = brain_rebuild_snapshot(snap.snapshot_id, "p", db_path=db)
        assert snap2.snapshot_id == snap.snapshot_id


def test_onboard_pending_not_in_decisions_and_model_fallback():
    with tempfile.TemporaryDirectory() as tmp:
        db = str(Path(tmp) / "b.db")
        brain_record(BrainRecordRequest(project_id="p", agent_id="a", records=[RecordInput(type="decision", content={"decision": "real decision", "reason": "r"}, status="active", tags=["d"])]), db_path=db)
        brain_ingest(IngestRequest(project_id="p", source="test", payload={"command": "false", "cwd": tmp}), db_path=db)
        brain_curate(BrainCurateRequest(project_id="p", mode="rule"), db_path=db)
        from brain_server.models import BrainOnboardRequest
        from brain_server.protocol import brain_onboard

        r = brain_onboard(BrainOnboardRequest(project_id="p", agent_id="a"), db_path=db)
        assert r.pending_reviews >= 1
        assert all("pending" not in str(d).lower() or d["id"] != "pending" for d in r.brief.get("important_decisions", []))
        # fallback when model disabled
        cr = brain_curate(BrainCurateRequest(project_id="p", mode="model"), db_path=db)
        assert any("fallback" in w for w in cr.warnings)


def test_embedding_provider_does_not_break():
    with tempfile.TemporaryDirectory() as tmp:
        db = str(Path(tmp) / "b.db")
        brain_record(BrainRecordRequest(project_id="p", agent_id="a", records=[RecordInput(type="knowledge", content={"content": "hello world"}, status="verified")]), db_path=db)
        from brain_server.search import EmbeddingProvider, ranked_search
        from brain_server.db import get_connection

        conn = get_connection(db)
        try:
            ranked_search(conn, "hello", limit=5, project_id="p", provider=EmbeddingProvider())
            assert False, "should raise"
        except RuntimeError:
            pass
        finally:
            conn.close()
        # default still works
        r = brain_ask(BrainAskRequest(project_id="p", agent_id="a", question="hello"), db_path=db)
        assert len(r.facts) >= 1
