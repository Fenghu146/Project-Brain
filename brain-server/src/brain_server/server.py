from __future__ import annotations

import json
from typing import Any

try:
    from mcp.server.mcpserver import MCPServer as _Srv

    mcp = _Srv("project-brain")
    _is_v2 = True
except ImportError:
    from mcp.server.fastmcp import FastMCP as _Srv  # type: ignore[no-redef]

    mcp = _Srv("project-brain")
    _is_v2 = False

from .models import BrainAskRequest, BrainHandoverRequest, BrainOnboardRequest, BrainRecordRequest, RecordInput


@mcp.tool()
def brain_onboard(project_id: str, agent_id: str, session_id: str | None = None, focus: str | None = None, token_budget: int = 1800) -> dict[str, Any]:
    from .protocol import brain_onboard as _onboard

    req = BrainOnboardRequest(project_id=project_id, agent_id=agent_id, session_id=session_id, focus=focus, token_budget=token_budget)
    resp = _onboard(req)
    return resp.model_dump()


@mcp.tool()
def brain_ask(project_id: str, agent_id: str, question: str, session_id: str | None = None, scope: list[str] | None = None, include_evidence: bool = True, limit: int = 8) -> dict[str, Any]:
    from .protocol import brain_ask as _ask

    req = BrainAskRequest(project_id=project_id, agent_id=agent_id, session_id=session_id, question=question, scope=scope, include_evidence=include_evidence, limit=limit)
    resp = _ask(req)
    return resp.model_dump()


@mcp.tool()
def brain_record(project_id: str, agent_id: str, records: list[dict[str, Any]], session_id: str | None = None) -> dict[str, Any]:
    from .protocol import brain_record as _record

    parsed = [RecordInput(**r) for r in records]
    req = BrainRecordRequest(project_id=project_id, agent_id=agent_id, session_id=session_id, records=parsed)
    resp = _record(req)
    return resp.model_dump()


@mcp.tool()
def brain_handover(
    project_id: str,
    agent_id: str,
    status: str,
    session_id: str | None = None,
    task_id: str | None = None,
    completed: list[str] | None = None,
    failed: list[str] | None = None,
    discovered: list[str] | None = None,
    remaining: list[str] | None = None,
    recommended_next_step: str | None = None,
    evidence_ids: list[str] | None = None,
) -> dict[str, Any]:
    from .protocol import brain_handover as _handover

    req = BrainHandoverRequest(
        project_id=project_id,
        agent_id=agent_id,
        session_id=session_id,
        task_id=task_id,
        status=status,  # type: ignore[arg-type]
        completed=completed or [],
        failed=failed or [],
        discovered=discovered or [],
        remaining=remaining or [],
        recommended_next_step=recommended_next_step,
        evidence_ids=evidence_ids or [],
    )
    resp = _handover(req)
    return resp.model_dump()


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
