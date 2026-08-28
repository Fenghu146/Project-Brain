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

from .models import BrainAskRequest, BrainCurateRequest, BrainHandoverRequest, BrainOnboardRequest, BrainRecordRequest, BrainReviewApplyRequest, BrainReviewListRequest, BrainSnapshotRequest, IngestRequest, RecordInput
from .db import get_connection
from .repository import create_link, get_links, list_evidence, get_memory, update_memory


@mcp.tool()
def brain_onboard(project_id: str, agent_id: str, session_id: str | None = None, focus: str | None = None, token_budget: int = 1800) -> dict[str, Any]:
    from .protocol import brain_onboard as _onboard

    req = BrainOnboardRequest(project_id=project_id, agent_id=agent_id, session_id=session_id, focus=focus, token_budget=token_budget)
    resp = _onboard(req)
    return resp.model_dump()


@mcp.tool()
def brain_ask(project_id: str, agent_id: str, question: str, session_id: str | None = None, scope: list[str] | None = None, include_evidence: bool = True, limit: int = 8, include_proposals: bool = False, as_of_commit: str | None = None, as_of_time: str | None = None) -> dict[str, Any]:
    from .protocol import brain_ask as _ask

    req = BrainAskRequest(project_id=project_id, agent_id=agent_id, session_id=session_id, question=question, scope=scope, include_evidence=include_evidence, limit=limit, include_proposals=include_proposals, as_of_commit=as_of_commit, as_of_time=as_of_time)
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


@mcp.tool()
def brain_verify(project_id: str, memory_id: str, action: str) -> dict[str, Any]:
    if action not in ("verify", "invalidate"):
        raise ValueError("action must be verify or invalidate")
    conn = get_connection()
    from .db import init_db

    init_db()
    conn = get_connection()
    try:
        mem = get_memory(conn, memory_id, project_id=project_id)
        if mem is None:
            raise ValueError(f"{memory_id} not found in {project_id}")
        new_status = "verified" if action == "verify" else "invalid"
        update_memory(conn, memory_id, status=new_status, project_id=project_id)
        conn.commit()
        return {"id": memory_id, "status": new_status}
    finally:
        conn.close()


@mcp.tool()
def brain_link(project_id: str, from_id: str, relation: str, to_id: str) -> dict[str, Any]:
    conn = get_connection()
    from .db import init_db

    init_db()
    conn = get_connection()
    try:
        create_link(conn, from_id, relation, to_id, project_id=project_id)
        conn.commit()
        return {"from": from_id, "relation": relation, "to": to_id, "project_id": project_id}
    finally:
        conn.close()


@mcp.tool()
def brain_get_links(project_id: str, from_id: str | None = None, to_id: str | None = None) -> dict[str, Any]:
    conn = get_connection()
    try:
        links = get_links(conn, from_id=from_id, to_id=to_id, project_id=project_id)
        return {"links": links}
    finally:
        conn.close()


@mcp.tool()
def brain_export(project_id: str | None = None, what: str = "all") -> dict[str, Any]:
    conn = get_connection()
    try:
        out: dict[str, Any] = {}
        if what in ("all", "memories"):
            q = "SELECT * FROM memories" + (" WHERE project_id=?" if project_id else "")
            params = [project_id] if project_id else []
            out["memories"] = [dict(r) for r in conn.execute(q, params).fetchall()]
        if what in ("all", "evidence"):
            q = "SELECT * FROM evidence" + (" WHERE project_id=?" if project_id else "")
            params = [project_id] if project_id else []
            out["evidence"] = [dict(r) for r in conn.execute(q, params).fetchall()]
        if what in ("all", "links"):
            q = "SELECT * FROM links" + (" WHERE project_id=?" if project_id else "")
            params = [project_id] if project_id else []
            out["links"] = [dict(r) for r in conn.execute(q, params).fetchall()]
        return out
    finally:
        conn.close()


@mcp.tool()
def brain_ingest(project_id: str, source: str, agent_id: str = "system", session_id: str | None = None, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    from .protocol import brain_ingest as _ingest

    req = IngestRequest(project_id=project_id, source=source, agent_id=agent_id, session_id=session_id, payload=payload or {})
    return _ingest(req)


@mcp.tool()
def brain_curate(project_id: str, event_ids: list[str] | None = None, mode: str = "rule", agent_id: str | None = None, session_id: str | None = None) -> dict[str, Any]:
    from .protocol import brain_curate as _curate

    req = BrainCurateRequest(project_id=project_id, event_ids=event_ids, mode=mode, agent_id=agent_id, session_id=session_id)
    return _curate(req).model_dump()


@mcp.tool()
def brain_review_list(project_id: str, status: str | None = None, limit: int = 20) -> dict[str, Any]:
    from .protocol import brain_review_list as _list

    req = BrainReviewListRequest(project_id=project_id, status=status, limit=limit)
    return _list(req).model_dump()


@mcp.tool()
def brain_review_apply(project_id: str, proposal_id: str, action: str, reviewer: str, reason: str | None = None) -> dict[str, Any]:
    from .protocol import brain_review_apply as _apply

    req = BrainReviewApplyRequest(project_id=project_id, proposal_id=proposal_id, action=action, reviewer=reviewer, reason=reason)  # type: ignore[arg-type]
    return _apply(req).model_dump()


@mcp.tool()
def brain_snapshot(project_id: str, basis_commit: str | None = None, basis_branch: str | None = None) -> dict[str, Any]:
    from .protocol import brain_snapshot as _snap

    req = BrainSnapshotRequest(project_id=project_id, basis_commit=basis_commit, basis_branch=basis_branch)
    return _snap(req).model_dump()


@mcp.tool()
def brain_rebuild_snapshot(project_id: str, snapshot_id: str) -> dict[str, Any]:
    from .protocol import brain_rebuild_snapshot as _rebuild

    return _rebuild(snapshot_id, project_id).model_dump()


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
