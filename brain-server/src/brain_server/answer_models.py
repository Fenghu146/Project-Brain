from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

# v0.6: Answer Brain types

IntentType = Literal[
    "project_goal",
    "current_state",
    "feature_summary",
    "mechanism_explanation",
    "decision_reason",
    "failure_experience",
    "evidence_trace",
    "version_history",
    "task_next_step",
    "test_result",
    "file_or_module_lookup",
    "generic_narrow",
    "generic_broad",
    "generic_search",
]

SourceClass = Literal[
    "user_confirmed",
    "verified_evidence",
    "active_decision",
    "active_knowledge",
    "project_model",
    "task_handover",
    "event_observation",
    "proposal",
    "generated_summary",
    "documentation_example",
]

PollutionTag = Literal[
    "code_block",
    "example_only",
    "version_plan",
    "test_summary",
    "current_state",
    "historical",
    "documentation_example",
]

SupportLevel = Literal["direct", "indirect", "weak", "none"]
FreshnessLevel = Literal["current", "historical", "stale", "future"]


class ConfidenceBreakdown(BaseModel):
    retrieval_confidence: float = 0.0
    source_confidence: float = 0.0
    evidence_coverage: float = 0.0
    freshness: float = 0.0
    conflict_penalty: float = 0.0

    def to_final(self) -> float:
        """Compute final confidence score with P2 improvements."""
        if self.retrieval_confidence == 0:
            return 0.0
        base = (
            self.retrieval_confidence * 0.25
            + self.source_confidence * 0.25
            + self.evidence_coverage * 0.3
            + self.freshness * 0.2
        )
        return round(max(0.0, min(1.0, base - self.conflict_penalty)), 2)


class KeyPoint(BaseModel):
    text: str
    source_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    support: SupportLevel = "none"
    source_class: SourceClass | None = None
    pollution_tags: list[PollutionTag] = Field(default_factory=list)
    is_stale: bool = False
    is_conflicted: bool = False
    # P2: New fields for finer granularity
    freshness: FreshnessLevel = "current"
    evidence_coverage: float = 0.0
    why_included: str | None = None
    why_excluded: str | None = None


class RelatedContext(BaseModel):
    id: str
    type: str
    status: str
    reason: str
    source_class: SourceClass | None = None


class NextAction(BaseModel):
    type: str
    command: str | None = None
    target_ids: list[str] = Field(default_factory=list)
    reason: str


class AnswerResult(BaseModel):
    schema_version: str = "0.6"
    answer: str
    key_points: list[KeyPoint] = Field(default_factory=list)
    facts: list[dict[str, Any]] = Field(default_factory=list)
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    related_context: list[RelatedContext] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)
    next_action: NextAction | None = None
    intent: IntentType = "generic_search"
    match_mode: str = "none"
    confidence: float = 0.0
    confidence_breakdown: ConfidenceBreakdown = Field(default_factory=ConfidenceBreakdown)
    provenance: list[dict[str, Any]] = Field(default_factory=list)
    # v0.5 compat fields
    stale_facts: list[dict[str, Any]] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)
    matches: list[dict[str, Any]] = Field(default_factory=list)
    proposals: list[dict[str, Any]] = Field(default_factory=list)
    # P1: Answer mode
    answer_mode: str = "standard"
    # P0: Version metadata
    brain_runtime_version: str = "0.6"
    workflow_version: str = "0.5"
    answer_version: str = "0.6"
    capability_schema_version: str = "4"
