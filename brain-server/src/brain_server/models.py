from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field

MemoryType = Literal["identity", "state", "knowledge", "experience", "decision", "task"]
MemoryStatus = Literal["draft", "proposed", "observed", "verified", "active", "deprecated", "invalid"]
TaskStatus = Literal["draft", "in_progress", "blocked", "completed", "cancelled"]
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
VALID_TASK_STATUSES = {"draft", "in_progress", "blocked", "completed", "cancelled"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class RecordInput(BaseModel):
    type: str
    content: dict[str, Any] | str
    status: str | None = None
    task_status: str | None = None
    confidence: float | None = None
    tags: list[str] | None = None
    evidence: list[dict[str, Any]] | None = None
    evidence_ids: list[str] | None = None
    origin: str | None = None
    valid_from: str | None = None
    valid_until: str | None = None
    branch: str | None = None
    commit_hash: str | None = None
    verification_due_at: str | None = None


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
    duplicate_of: list[dict[str, Any]] = Field(default_factory=list)
    conflicts: list[dict[str, Any]] = Field(default_factory=list)
    schema_version: str = "4"


class BrainAskRequest(BaseModel):
    project_id: str
    agent_id: str
    session_id: str | None = None
    question: str
    scope: list[str] | None = None
    include_evidence: bool = True
    limit: int = 8
    include_proposals: bool = False
    as_of_commit: str | None = None
    as_of_time: str | None = None


class BrainAskResponse(BaseModel):
    answer: str
    facts: list[dict[str, Any]] = Field(default_factory=list)
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)
    confidence: float | None = None
    match_mode: str = "none"
    matches: list[dict[str, Any]] = Field(default_factory=list)
    proposals: list[dict[str, Any]] = Field(default_factory=list)
    stale_facts: list[dict[str, Any]] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)
    schema_version: str = "4"


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
    evidence_ids: list[str] = Field(default_factory=list)
    missing_context: list[str] = Field(default_factory=list)
    pending_reviews: int = 0
    stale_context: list[dict[str, Any]] = Field(default_factory=list)
    verification_suggestions: list[str] = Field(default_factory=list)
    basis_commit: str | None = None
    confidence: float | None = None
    schema_version: str = "4"


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
    pending_proposals_count: int = 0
    verification_suggestions: list[str] = Field(default_factory=list)
    basis_commit: str | None = None
    model_snapshot_id: str | None = None
    schema_version: str = "4"


class ProposalAction(str):
    pass


ProposalStatus = Literal["pending", "approved", "rejected", "deferred", "superseded"]
ReviewAction = Literal["approved", "rejected", "deferred", "superseded"]


class BrainCurateRequest(BaseModel):
    project_id: str
    agent_id: str | None = None
    session_id: str | None = None
    event_ids: list[str] | None = None
    mode: str = "rule"


class BrainCurateResponse(BaseModel):
    created: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class BrainReviewListRequest(BaseModel):
    project_id: str
    status: str | None = None
    limit: int = 20


class BrainReviewListResponse(BaseModel):
    proposals: list[dict[str, Any]] = Field(default_factory=list)


class BrainReviewApplyRequest(BaseModel):
    project_id: str
    proposal_id: str
    action: ReviewAction
    reviewer: str
    reason: str | None = None


class BrainReviewApplyResponse(BaseModel):
    proposal: dict[str, Any]
    applied_event_id: str | None = None


class BrainSnapshotRequest(BaseModel):
    project_id: str
    basis_commit: str | None = None
    basis_branch: str | None = None


class BrainSnapshotResponse(BaseModel):
    snapshot_id: str
    model_json: dict[str, Any]
    source_ids: list[str] = Field(default_factory=list)
    confidence: float | None = None


class IngestRequest(BaseModel):
    project_id: str
    source: str
    agent_id: str = "system"
    session_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


def content_to_text(content: dict[str, Any] | str) -> str:
    if isinstance(content, str):
        return content
    return json.dumps(content, ensure_ascii=False)


_CN_CHAR = re.compile(r"[\u4e00-\u9fff]")


def expand_chinese_bigrams(text: str) -> str:
    """Insert space-joined bigrams for contiguous CJK runs so unicode61 FTS5
    can index and match Chinese phrases. Non-CJK tokens pass through unchanged.

    "Vinyl 黑胶收藏管理" → "Vinyl 黑胶 胶收 收藏 藏管 管理 黑胶收藏管理"
    "身份" → "身份 身份"   (duplicate single-char to balance FTS weighting)
    """
    if not text:
        return text

    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        if _CN_CHAR.match(text[i]):
            j = i
            while j < n and _CN_CHAR.match(text[j]):
                j += 1
            run = text[i:j]
            if len(run) == 1:
                out.append(f"{run} {run}")
            else:
                bigrams = " ".join(run[k : k + 2] for k in range(len(run) - 1))
                out.append(f"{bigrams} {run}")
            i = j
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


def build_fts_query(query: str) -> str:
    """Transform a user query into an FTS5 MATCH expression that handles
    CJK via bigram OR-of-terms. Latin tokens are passed through with a `*`
    prefix wildcard for partial match.
    """
    if not query or not query.strip():
        return ""
    raw = query.replace("？", " ").replace("?", " ").replace("，", " ").replace(",", " ").strip()
    toks = [t for t in raw.split() if t.strip()]
    if not toks:
        toks = [raw]
    fts_terms: list[str] = []
    for t in toks:
        if _CN_CHAR.search(t):
            bigrams = expand_chinese_bigrams(t).split()
            fts_terms.extend(bigrams)
            if len(t) == 1:
                fts_terms.append(t)
        else:
            cleaned = t.replace('"', "")
            if cleaned:
                fts_terms.append(f'"{cleaned}"*')
    seen: set[str] = set()
    uniq: list[str] = []
    for term in fts_terms:
        if term not in seen:
            seen.add(term)
            uniq.append(term)
    return " OR ".join(uniq) if uniq else ""


class ConflictError(Exception):
    """Raised when optimistic lock conflict detected."""
    pass


def validate_memory_type(t: str) -> str:
    if t not in VALID_MEMORY_TYPES:
        raise ValueError(f"invalid memory type: {t}, expected one of {sorted(VALID_MEMORY_TYPES)}")
    return t


FeedbackVerdict = Literal["accepted", "corrected", "expanded", "irrelevant", "missing_evidence"]


class BrainFeedbackRequest(BaseModel):
    project_id: str
    agent_id: str
    session_id: str | None = None
    question: str
    answer_claim_ids: list[str] | None = None
    intent: str | None = None
    confidence: float | None = None
    verdict: FeedbackVerdict
    corrected_text: str | None = None


class BrainFeedbackResponse(BaseModel):
    feedback_id: str
    event_id: str


def validate_memory_status(s: str) -> str:
    if s not in VALID_MEMORY_STATUSES:
        raise ValueError(f"invalid status: {s}")
    return s
