from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal


AutomationTrigger = Literal["session_start", "git_commit", "git_status", "test_run", "file_change", "agent_note", "session_end"]
SessionStatus = Literal["active", "paused", "completed", "abandoned"]
AutomationStatus = Literal["running", "completed", "partial", "failed", "skipped"]
HandoverDraftStatus = Literal["pending", "applied", "discarded"]


@dataclass
class SessionStart:
    session_id: str
    project_id: str
    agent_id: str
    basis_commit: str | None
    basis_branch: str | None
    context: dict[str, Any]
    source_ids: list[str]
    warnings: list[str] = field(default_factory=list)


@dataclass
class ObservationReceipt:
    observation_id: str
    session_id: str
    event_ids: list[str]
    evidence_ids: list[str]
    proposal_ids: list[str]
    warnings: list[str] = field(default_factory=list)
    automation_run_id: str | None = None


@dataclass
class HandoverDraft:
    draft_id: str
    session_id: str
    task_id: str | None
    status: HandoverDraftStatus
    report: dict[str, Any]
    source_event_ids: list[str]
    proposal_ids: list[str]
    basis_commit: str | None
    applied_handover_id: str | None = None
    warnings: list[str] = field(default_factory=list)


@dataclass
class AutomationRun:
    run_id: str
    project_id: str
    session_id: str
    trigger: AutomationTrigger
    status: AutomationStatus
    started_at: datetime
    finished_at: datetime | None = None
    created_event_ids: list[str] = field(default_factory=list)
    created_evidence_ids: list[str] = field(default_factory=list)
    created_proposal_ids: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    error: str | None = None


@dataclass
class WorkflowConfig:
    project_id: str
    automation_mode: Literal["full", "observe_only", "disabled"] = "full"
    exclude_paths: list[str] = field(default_factory=list)
    redact_patterns: list[str] = field(default_factory=list)
    max_payload_bytes: int = 8192
    retain_raw_output: bool = False
    debounce_seconds: int = 30
    buffer_max_items: int = 1000
    buffer_ttl_minutes: int = 30
