from __future__ import annotations

from typing import Any

# Automation level strategy for v0.5
# L0: auto-write Event/Evidence only
# L1: auto-generate pending Proposal
# L2: explicit confirm required (disabled by default)

AUTO_LEVELS: dict[str, str] = {
    "session_start": "L0",
    "git_commit": "L0",
    "git_status": "L0",
    "test_run": "L0",
    "file_change": "L1",
    "agent_note": "L1",
    "session_end": "L1",
    "model_curator": "L2_off",  # Disabled by default
}


def get_auto_level(trigger: str) -> str:
    """Get automation level for a trigger."""
    return AUTO_LEVELS.get(trigger, "L0")


def is_auto_allowed(trigger: str, mode: str = "full") -> bool:
    """Check if auto-action is allowed based on mode."""
    if mode == "disabled":
        return False
    if mode == "observe_only":
        level = get_auto_level(trigger)
        return level in ("L0", "L1")
    return True  # full mode


def explain_action(
    run_id: str,
    project_id: str,
    trigger: str,
    session_id: str,
    created_event_ids: list[str],
    created_evidence_ids: list[str],
    created_proposal_ids: list[str],
    warnings: list[str],
    error: str | None = None,
) -> dict[str, Any]:
    """Generate explanation for an automation run."""
    return {
        "run_id": run_id,
        "project_id": project_id,
        "trigger": trigger,
        "session_id": session_id,
        "created_events": len(created_event_ids),
        "created_evidence": len(created_evidence_ids),
        "created_proposals": len(created_proposal_ids),
        "warnings": warnings,
        "error": error,
        "explanation": f"Trigger '{trigger}' produced {len(created_event_ids)} events, {len(created_evidence_ids)} evidence, {len(created_proposal_ids)} proposals",
    }
