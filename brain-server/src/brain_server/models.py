from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field

MemoryType = Literal["identity", "state", "knowledge", "experience", "decision", "task"]
MemoryStatus = Literal["draft", "proposed", "observed", "verified", "active", "deprecated", "invalid"]
EvidenceType = Literal["test_result", "log", "file", "commit", "user_confirmation", "observed"]
EvidenceStatus = Literal["observed", "verified", "invalid"]
EventAction = Literal["modify_file", "run_test", "record", "handover", "onboard", "create", "update"]
HandoverStatus = Literal["completed", "partial", "failed"]
LinkRelation = Literal["supports", "supersedes", "conflicts_with", "related_to", "evidence_of", "source_event"]

MEMORY_PREFIX: dict[str, str] = {
    "identity": "I",
    "state": "S",
    "knowledge": "K",
    "experience": "X",
    "decision": "D",
    "task": "T",
}
EVIDENCE_PREFIX = "E"
EVENT_PREFIX = "EV"
HANDOVER_PREFIX = "H"

VALID_MEMORY_TYPES = set(MEMORY_PREFIX.keys())
VALID_MEMORY_STATUSES = {"draft", "proposed", "observed", "verified", "active", "deprecated", "invalid"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class RecordInput(BaseModel):
    type: str
    content: dict[str, Any] | str
    status: str | None = None
    confidence: float | None = None
    tags: list[str] | None = None
    evidence: list[dict[str, Any]] | None = None


class BrainRecordRequest(BaseModel):
    project_id: str
    agent_id: str
    session_id: str | None = None
    records: list[RecordInput]


class BrainRecordResponse(BaseModel):
    accepted: list[str] = Field(default_factory=list)
    deduplicated: list[str] = Field(default_factory=list)
    needs_verification: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    state_updates: list[str] = Field(default_factory=list)


class BrainAskRequest(BaseModel):
    project_id: str
    agent_id: str
    session_id: str | None = None
    question: str
    scope: list[str] | None = None
    include_evidence: bool = True
    limit: int = 8


class BrainAskResponse(BaseModel):
    answer: str
    facts: list[dict[str, Any]] = Field(default_factory=list)
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)
    confidence: float | None = None


class BrainOnboardRequest(BaseModel):
    project_id: str
    agent_id: str
    session_id: str | None = None
    focus: str | None = None
    token_budget: int = 1800


class BrainOnboardResponse(BaseModel):
    project_id: str
    generated_at: str
    brief: dict[str, Any]
    source_ids: list[str] = Field(default_factory=list)
    confidence: float | None = None


class BrainHandoverRequest(BaseModel):
    project_id: str
    agent_id: str
    session_id: str | None = None
    task_id: str | None = None
    status: HandoverStatus
    completed: list[str] = Field(default_factory=list)
    failed: list[str] = Field(default_factory=list)
    discovered: list[str] = Field(default_factory=list)
    remaining: list[str] = Field(default_factory=list)
    recommended_next_step: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)


class BrainHandoverResponse(BaseModel):
    handover_id: str
    report: dict[str, Any]
    brain_updates: list[str] = Field(default_factory=list)


def content_to_text(content: dict[str, Any] | str) -> str:
    if isinstance(content, str):
        return content
    return json.dumps(content, ensure_ascii=False)


def validate_memory_type(t: str) -> str:
    if t not in VALID_MEMORY_TYPES:
        raise ValueError(f"invalid memory type: {t}, expected one of {sorted(VALID_MEMORY_TYPES)}")
    return t


def validate_memory_status(s: str) -> str:
    if s not in VALID_MEMORY_STATUSES:
        raise ValueError(f"invalid status: {s}")
    return s
