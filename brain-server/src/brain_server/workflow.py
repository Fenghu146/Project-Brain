from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import BrainAskRequest, BrainOnboardRequest, BrainRecordRequest, BrainReviewListRequest, IngestRequest, RecordInput, now_iso
from .protocol import brain_ask, brain_curate, brain_handover, brain_onboard, brain_record, brain_review_list, brain_ingest
from .repository import create_handover_draft, create_session, get_connection, get_handover_draft, get_session, list_automation_runs, list_events, list_handover_drafts, list_memories, list_proposals, update_automation_run, update_handover_draft_status, update_session_status
from .workflow_models import (
    AutomationRun,
    AutomationStatus,
    AutomationTrigger,
    HandoverDraft,
    HandoverDraftStatus,
    ObservationReceipt,
    SessionStart,
    WorkflowConfig,
)


class WorkflowBrainError(Exception):
    """Base exception for WorkflowBrain errors."""
    pass


class BrainUnavailableError(WorkflowBrainError):
    """Raised when Brain database is not accessible."""
    pass


class WorkflowBrain:
    """
    WorkflowBrain is the top-level orchestrator for session-based automation.

    It provides three main entry points:
    - start_session(): Initialize a new session and return compact context
    - observe(): Process an observation and return receipt
    - end_session(): Generate handover draft from session activities
    """

    def __init__(self, db_path: str | None = None, config: WorkflowConfig | None = None):
        self.db_path = db_path
        self.config = config or WorkflowConfig(project_id="default")
        self._buffer: dict[str, dict[str, list[dict]]] = {}  # project_id -> session_id -> List[raw_obs]
        self._locks: dict[str, threading.Lock] = {}

    def start_session(
        self,
        project_id: str,
        agent_id: str,
        context: dict[str, Any] | None = None,
        level: str = "compact",
        session_id: str | None = None,
        automation_mode: str = "full",
    ) -> SessionStart:
        """
        Start a new session and return lightweight context.

        Args:
            project_id: Project identifier
            agent_id: Agent identifier
            context: Optional initial context overrides
            level: "compact" | "focused" | "full"
            session_id: Optional explicit session ID (for idempotency)

        Returns:
            SessionStart with compact context

        Raises:
            BrainUnavailableError: If Brain is not accessible
        """
        try:
            conn = get_connection(self.db_path)
        except Exception as e:
            raise BrainUnavailableError(f"Cannot connect to Brain: {e}")

        try:
            # Get project root from config
            cfg_path = Path(self.db_path).parent / "config.json" if self.db_path else None
            project_root = None
            if cfg_path and cfg_path.exists():
                try:
                    cfg = json.loads(cfg_path.read_text())
                    project_root = cfg.get("project_root") or str(Path(self.db_path).parent.parent)
                except Exception:
                    pass

            # Get basis commit/branch
            basis_commit = None
            basis_branch = None
            try:
                import subprocess
                if project_root:
                    basis_commit = subprocess.check_output(
                        ["git", "rev-parse", "HEAD"],
                        cwd=str(project_root),
                        text=True,
                        timeout=3,
                    ).strip() or None
                    basis_branch = subprocess.check_output(
                        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                        cwd=str(project_root),
                        text=True,
                        timeout=3,
                    ).strip() or None
            except Exception:
                pass

            # Build context based on level
            ctx = self._build_context(project_id, level, basis_commit)

            # Create or get session
            sess_id = session_id or f"session-{int(time.time())}"
            existing = get_session(conn, sess_id, project_id)
            if existing:
                # Session already exists, return existing
                pass
            else:
                create_session(conn, sess_id, project_id, agent_id, basis_commit, basis_branch, automation_mode)
                conn.commit()

            # Return SessionStart
            return SessionStart(
                session_id=sess_id,
                project_id=project_id,
                agent_id=agent_id,
                basis_commit=basis_commit,
                basis_branch=basis_branch,
                context=ctx,
                source_ids=ctx.get("source_ids", []),
                warnings=[],
            )
        finally:
            conn.close()

    def observe(
        self,
        project_id: str,
        observation: dict[str, Any],
        session_id: str,
        trigger: AutomationTrigger = "agent_note",
    ) -> ObservationReceipt:
        """
        Process an observation and return receipt.

        Args:
            project_id: Project identifier
            observation: Observation dict with kind, source, result, payload
            session_id: Current session ID
            trigger: Trigger type

        Returns:
            ObservationReceipt with created IDs
        """
        # Normalize observation
        normalized = self._normalize_observation(observation)

        # Check privacy
        if self._should_skip(normalized):
            return ObservationReceipt(
                observation_id=normalized.get("id", "unknown"),
                session_id=session_id,
                event_ids=[],
                evidence_ids=[],
                proposal_ids=[],
                warnings=["Observation skipped due to privacy rules"],
            )

        # Create automation run
        run_id = f"AR-{len(self._buffer.get(project_id, {}).get(session_id, [])) + 1:03d}"

        try:
            # Delegate to existing ingest protocols
            event_ids = []
            evidence_ids = []
            proposal_ids = []
            warnings = []

            kind = normalized.get("kind", "agent_note")

            if kind == "git":
                result = brain_ingest(IngestRequest(
                    project_id=project_id,
                    source="git",
                    agent_id="workflow",
                    session_id=session_id,
                    payload=normalized.get("payload", {}),
                ), db_path=self.db_path)
                event_ids.append(result.get("event_id", ""))
            elif kind == "test":
                result = brain_ingest(IngestRequest(
                    project_id=project_id,
                    source="test",
                    agent_id="workflow",
                    session_id=session_id,
                    payload={
                        "command": normalized.get("source", ""),
                        "cwd": normalized.get("payload", {}).get("cwd", "."),
                    },
                ), db_path=self.db_path)
                event_ids.append(result.get("event_id", ""))
                if normalized.get("result") == "failed":
                    # Generate proposal for failure
                    proposal_ids.append(f"P-{len(proposal_ids) + 1:03d}")
            elif kind == "file":
                result = brain_ingest(IngestRequest(
                    project_id=project_id,
                    source="file",
                    agent_id="workflow",
                    session_id=session_id,
                    payload={"path": normalized.get("source", "")},
                ), db_path=self.db_path)
                event_ids.append(result.get("event_id", ""))
            elif kind == "agent_note":
                # Write as evidence or event based on content
                if normalized.get("payload", {}).get("type") == "evidence":
                    result = brain_record(BrainRecordRequest(
                        project_id=project_id,
                        agent_id="workflow",
                        session_id=session_id,
                        records=[RecordInput(
                            type="evidence",
                            content=normalized.get("payload", {}),
                        )],
                    ), db_path=self.db_path)
                    evidence_ids.extend(result.accepted)
                else:
                    result = brain_ingest(IngestRequest(
                        project_id=project_id,
                        source="record",
                        agent_id="workflow",
                        session_id=session_id,
                        payload=normalized.get("payload", {}),
                    ), db_path=self.db_path)
                    event_ids.append(result.get("event_id", ""))

            return ObservationReceipt(
                observation_id=normalized.get("id", f"OBS-{int(time.time())}"),
                session_id=session_id,
                event_ids=event_ids,
                evidence_ids=evidence_ids,
                proposal_ids=proposal_ids,
                warnings=warnings,
                automation_run_id=run_id,
            )
        except Exception as e:
            return ObservationReceipt(
                observation_id=normalized.get("id", "unknown"),
                session_id=session_id,
                event_ids=[],
                evidence_ids=[],
                proposal_ids=[],
                warnings=[f"Observation processing failed: {e}"],
            )

    def end_session(
        self,
        project_id: str,
        session_id: str,
        user_input: dict[str, Any] | None = None,
        task_id: str | None = None,
    ) -> HandoverDraft:
        """
        End a session and generate handover draft.

        Args:
            project_id: Project identifier
            session_id: Session to end
            user_input: Optional user-provided summary

        Returns:
            HandoverDraft with generated report
        """
        try:
            conn = get_connection(self.db_path)
        except Exception as e:
            raise BrainUnavailableError(f"Cannot connect to Brain: {e}")

        try:
            # Get session events
            events = list_events(conn, project_id=project_id, limit=100)
            session_events = [e for e in events if e.get("session_id") == session_id]

            # Get active tasks
            tasks = list_memories(conn, project_id=project_id, mem_type="task", limit=10)
            active_tasks = [t for t in tasks if t.get("task_status") in ("in_progress", "blocked")]

            # Get pending proposals
            proposals = list_proposals(conn, project_id=project_id, status="pending", limit=20)

            # Build draft report
            report = {
                "session_id": session_id,
                "generated_at": now_iso(),
                "events_count": len(session_events),
                "proposals_count": len(proposals),
                "active_tasks": [t["id"] for t in active_tasks[:3]],
            }

            # Add user input if provided
            if user_input:
                report.update(user_input)

            # Generate draft ID
            draft_id = f"HD-{int(time.time())}"

            # Create handover draft in DB
            create_handover_draft(
                conn,
                draft_id=draft_id,
                project_id=project_id,
                session_id=session_id,
                task_id=task_id,
                report=report,
                source_event_ids=[e["id"] for e in session_events],
                proposal_ids=[p["id"] for p in proposals],
            )
            conn.commit()

            # Update session status
            update_session_status(conn, session_id, project_id, "completed")
            conn.commit()

            return HandoverDraft(
                draft_id=draft_id,
                session_id=session_id,
                task_id=task_id,
                status="pending",
                report=report,
                source_event_ids=[e["id"] for e in session_events],
                proposal_ids=[p["id"] for p in proposals],
                basis_commit=None,
            )
        finally:
            conn.close()

    # Private methods

    def _build_context(
        self,
        project_id: str,
        level: str,
        basis_commit: str | None,
    ) -> dict[str, Any]:
        """Build context based on level."""
        if level == "full":
            # Use existing onboard for full context
            resp = brain_onboard(BrainOnboardRequest(
                project_id=project_id,
                agent_id="workflow",
                session_id="system",
            ), db_path=self.db_path)
            return resp.brief
        elif level == "focused":
            # Compact + tasks + decisions + failures
            resp = brain_onboard(BrainOnboardRequest(
                project_id=project_id,
                agent_id="workflow",
                session_id="system",
                token_budget=800,
            ), db_path=self.db_path)
            brief = resp.brief
            return {
                "identity": brief.get("identity"),
                "current_goal": brief.get("current_state"),
                "next_step": brief.get("recommended_next_step"),
                "blockers": brief.get("blockers", []),
                "pending_reviews": brief.get("pending_reviews", 0),
                "stale_count": len(brief.get("stale_context", [])),
                "basis_commit": basis_commit,
                "source_ids": resp.source_ids,
                "important_decisions": brief.get("important_decisions", []),
                "active_tasks": brief.get("active_tasks", []),
            }
        else:  # compact
            # Minimal context
            resp = brain_onboard(BrainOnboardRequest(
                project_id=project_id,
                agent_id="workflow",
                session_id="system",
                token_budget=400,
            ), db_path=self.db_path)
            brief = resp.brief
            return {
                "identity": brief.get("identity", "")[:100],
                "current_goal": brief.get("current_state", "")[:100],
                "next_step": brief.get("recommended_next_step", ""),
                "blockers": brief.get("blockers", [])[:2],
                "pending_reviews": brief.get("pending_reviews", 0),
                "stale_count": len(brief.get("stale_context", [])),
                "basis_commit": basis_commit,
                "source_ids": resp.source_ids[:3],
            }

    def _normalize_observation(self, obs: dict[str, Any]) -> dict[str, Any]:
        """Normalize observation to standard format."""
        return {
            "id": obs.get("id", f"OBS-{hashlib.sha256(json.dumps(obs, sort_keys=True).encode()).hexdigest()[:8]}"),
            "kind": obs.get("kind", "agent_note"),
            "source": obs.get("source", ""),
            "result": obs.get("result", "observed"),
            "payload": obs.get("payload", {}),
            "timestamp": obs.get("timestamp", now_iso()),
        }

    def _should_skip(self, observation: dict[str, Any]) -> bool:
        """Check if observation should be skipped due to privacy rules."""
        source = observation.get("source", "")
        payload = observation.get("payload", {})

        # Check exclude_paths
        for pattern in self.config.exclude_paths:
            if pattern in source or pattern in str(payload):
                return True

        # Check redact_patterns
        content = json.dumps({**observation, **payload})
        for pattern in self.config.redact_patterns:
            if re.search(pattern, content, re.IGNORECASE):
                return True

        # Check max_payload_bytes
        if len(content.encode()) > self.config.max_payload_bytes:
            return True

        return False
