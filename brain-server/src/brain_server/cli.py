from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .db import DEFAULT_DB_PATH, init_db
from .models import BrainAskRequest, BrainHandoverRequest, BrainOnboardRequest, BrainRecordRequest, RecordInput


def resolve_db(db_arg: str | None) -> Path:
    if db_arg:
        return Path(db_arg).expanduser().resolve()
    cur = Path.cwd().resolve()
    for p in [cur, *cur.parents]:
        if (p / ".brain" / "brain.db").exists() or (p / ".brain" / "config.json").exists():
            return (p / ".brain" / "brain.db").resolve()
    return (Path.cwd() / ".brain" / "brain.db").resolve()


def resolve_project_id(db_path: Path, explicit: str | None) -> str | None:
    if explicit:
        return explicit
    cfg = db_path.parent / "config.json"
    if cfg.exists():
        try:
            return json.loads(cfg.read_text(encoding="utf-8")).get("project_id")
        except Exception:
            return None
    return None


def cmd_init(args: argparse.Namespace) -> int:
    from datetime import datetime, timezone

    target_dir = Path(args.dir).expanduser().resolve() if args.dir else Path.cwd().resolve()
    brain_dir = target_dir / ".brain"
    db_path = Path(args.db).expanduser().resolve() if args.db else brain_dir / "brain.db"
    project_id = args.project or target_dir.name

    conn = init_db(str(db_path))
    conn.close()
    cfg_path = db_path.parent / "config.json"
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg = {"project_id": project_id, "created_at": datetime.now(timezone.utc).isoformat(), "version": "0.1.0"}
    cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Brain initialized: {db_path}")
    print(f"Config: {cfg_path} (project_id={project_id})")

    if args.seed:
        from .protocol import brain_record

        seed = BrainRecordRequest(
            project_id=project_id,
            agent_id="system",
            session_id="init",
            records=[
                RecordInput(type="identity", content={"name": project_id, "purpose": args.purpose or f"{project_id} project"}, status="active", tags=[project_id]),
                RecordInput(type="state", content={"current_goal": "初始化完成，待写入首个任务", "phase": "init", "blockers": [], "open_questions": [], "recent_changes": ["brain init"]}, status="active"),
                RecordInput(type="task", content={"title": "首个任务（占位）", "status": "draft", "remaining": ["明确首个目标"], "next_step": "明确首个目标"}, status="draft"),
            ],
        )
        resp = brain_record(seed, db_path=str(db_path))
        print(f"Seed: accepted={resp.accepted}")

    return 0


def cmd_onboard(args: argparse.Namespace) -> int:
    from .protocol import brain_onboard

    db_path = resolve_db(args.db)
    project_id = resolve_project_id(db_path, args.project)
    if not project_id:
        print("error: --project is required (no config found)", file=sys.stderr)
        return 2
    req = BrainOnboardRequest(project_id=project_id, agent_id=args.agent, session_id=args.session, focus=args.focus, token_budget=args.token_budget)
    resp = brain_onboard(req, db_path=str(db_path))
    print(json.dumps(resp.model_dump(), ensure_ascii=False, indent=2))
    return 0


def cmd_ask(args: argparse.Namespace) -> int:
    from .protocol import brain_ask

    db_path = resolve_db(args.db)
    project_id = resolve_project_id(db_path, args.project)
    if not project_id:
        print("error: --project is required", file=sys.stderr)
        return 2
    scope = [s.strip() for s in args.scope.split(",") if s.strip()] if args.scope else None
    req = BrainAskRequest(project_id=project_id, agent_id=args.agent, session_id=args.session, question=args.question, scope=scope, include_evidence=not args.no_evidence, limit=args.limit)
    resp = brain_ask(req, db_path=str(db_path))
    print(json.dumps(resp.model_dump(), ensure_ascii=False, indent=2))
    return 0


def cmd_record(args: argparse.Namespace) -> int:
    from .protocol import brain_record

    db_path = resolve_db(args.db)
    project_id = resolve_project_id(db_path, args.project)
    if not project_id:
        print("error: --project is required", file=sys.stderr)
        return 2

    records: list[RecordInput] = []
    if args.file:
        data = json.loads(Path(args.file).read_text(encoding="utf-8"))
        raw_records = data if isinstance(data, list) else data.get("records", [])
        for r in raw_records:
            records.append(RecordInput(**r))
    else:
        if not args.type or not args.content:
            print("error: --type and --content are required (or use --file)", file=sys.stderr)
            return 2
        try:
            content = json.loads(args.content)
        except Exception:
            content = args.content
        tags = [t.strip() for t in args.tags.split(",") if t.strip()] if args.tags else None
        evidence = json.loads(args.evidence) if args.evidence else None
        evidence_ids = [s.strip() for s in args.evidence_ids.split(",") if s.strip()] if getattr(args, "evidence_ids", None) else None
        records.append(RecordInput(type=args.type, content=content, status=args.status, task_status=getattr(args, "task_status", None), confidence=args.confidence, tags=tags, evidence=evidence, evidence_ids=evidence_ids))

    req = BrainRecordRequest(project_id=project_id, agent_id=args.agent, session_id=args.session, records=records)
    resp = brain_record(req, db_path=str(db_path))
    print(json.dumps(resp.model_dump(), ensure_ascii=False, indent=2))
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    from .db import get_connection
    from .repository import update_memory, get_memory

    db_path = resolve_db(args.db)
    project_id = resolve_project_id(db_path, args.project)
    if not project_id:
        print("error: --project is required", file=sys.stderr)
        return 2
    conn = get_connection(str(db_path))
    try:
        mem = get_memory(conn, args.id, project_id=project_id)
        if mem is None:
            print(f"not found: {args.id} in {project_id}", file=sys.stderr)
            return 1
        new_status = "verified" if args.action == "verify" else "invalid"
        update_memory(conn, args.id, status=new_status, project_id=project_id)
        conn.commit()
        print(json.dumps({"id": args.id, "status": new_status}, ensure_ascii=False, indent=2))
    finally:
        conn.close()
    return 0


def cmd_link(args: argparse.Namespace) -> int:
    from .db import get_connection
    from .repository import create_link

    db_path = resolve_db(args.db)
    project_id = resolve_project_id(db_path, args.project)
    if not project_id:
        print("error: --project is required", file=sys.stderr)
        return 2
    conn = get_connection(str(db_path))
    try:
        create_link(conn, args.from_id, args.relation, args.to_id, project_id=project_id)
        conn.commit()
        print(json.dumps({"from": args.from_id, "relation": args.relation, "to": args.to_id, "project_id": project_id}, ensure_ascii=False, indent=2))
    finally:
        conn.close()
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    from .db import get_connection
    import json as _json

    db_path = resolve_db(args.db)
    project_id = resolve_project_id(db_path, args.project) if getattr(args, "project", None) else None
    conn = get_connection(str(db_path))
    try:
        out: dict = {}
        if args.what in ("all", "memories"):
            q = "SELECT * FROM memories"
            params: list = []
            if project_id:
                q += " WHERE project_id=?"
                params.append(project_id)
            out["memories"] = [dict(r) for r in conn.execute(q, params).fetchall()]
        if args.what in ("all", "evidence"):
            q = "SELECT * FROM evidence"
            params = []
            if project_id:
                q += " WHERE project_id=?"
                params.append(project_id)
            out["evidence"] = [dict(r) for r in conn.execute(q, params).fetchall()]
        if args.what in ("all", "links"):
            q = "SELECT * FROM links"
            params = []
            if project_id:
                q += " WHERE project_id=?"
                params.append(project_id)
            out["links"] = [dict(r) for r in conn.execute(q, params).fetchall()]
        if args.what in ("all", "events"):
            q = "SELECT * FROM events"
            params = []
            if project_id:
                q += " WHERE project_id=?"
                params.append(project_id)
            out["events"] = [dict(r) for r in conn.execute(q, params).fetchall()]
        if args.what in ("all", "handovers"):
            q = "SELECT * FROM handovers"
            params = []
            if project_id:
                q += " WHERE project_id=?"
                params.append(project_id)
            out["handovers"] = [dict(r) for r in conn.execute(q, params).fetchall()]
        dest = Path(args.out) if args.out else None
        txt = _json.dumps(out, ensure_ascii=False, indent=2)
        if dest:
            dest.write_text(txt, encoding="utf-8")
            print(f"exported to {dest}")
        else:
            print(txt)
    finally:
        conn.close()
    return 0


def cmd_handover(args: argparse.Namespace) -> int:
    from .protocol import brain_handover

    db_path = resolve_db(args.db)
    project_id = resolve_project_id(db_path, args.project)
    if not project_id:
        print("error: --project is required", file=sys.stderr)
        return 2

    def _list(v: str | None) -> list[str]:
        if not v:
            return []
        try:
            parsed = json.loads(v)
            if isinstance(parsed, list):
                return [str(x) for x in parsed]
        except Exception:
            pass
        return [s.strip() for s in v.split(",") if s.strip()]

    completed = _list(args.completed)
    failed = _list(args.failed)
    discovered = _list(args.discovered)
    remaining = _list(args.remaining)
    evidence_ids = _list(args.evidence_ids)

    req = BrainHandoverRequest(
        project_id=project_id,
        agent_id=args.agent,
        session_id=args.session,
        task_id=args.task,
        status=args.status,  # type: ignore[arg-type]
        completed=completed,
        failed=failed,
        discovered=discovered,
        remaining=remaining,
        recommended_next_step=args.next_step,
        evidence_ids=evidence_ids,
    )
    resp = brain_handover(req, db_path=str(db_path))
    print(json.dumps(resp.model_dump(), ensure_ascii=False, indent=2))
    md = db_path.parent / "exports" / "latest-handover.md"
    if md.exists():
        print(f"\nMarkdown: {md}", file=sys.stderr)
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    db_path = resolve_db(args.db)
    if not db_path.exists():
        print(f"no brain at {db_path} — run `brain init` first", file=sys.stderr)
        return 1
    from .db import get_connection

    project_id = resolve_project_id(db_path, getattr(args, "project", None))
    conn = get_connection(str(db_path))
    where = " WHERE project_id=?" if project_id else ""
    params: list[str] = [project_id] if project_id else []
    cur = conn.execute(f"SELECT type, status, task_status, count(*) as c FROM memories{where} GROUP BY type, status, task_status ORDER BY type", params)
    rows = cur.fetchall()
    print(f"DB: {db_path}")
    cfg = db_path.parent / "config.json"
    if cfg.exists():
        print(f"Config: {cfg.read_text(encoding='utf-8')}")
    if project_id:
        print(f"Project filter: {project_id}")
    print("Memories:")
    for r in rows:
        ts = f" task_status={r['task_status']}" if r["task_status"] else ""
        print(f"  {r['type']:12} {r['status']:10}{ts}  {r['c']}")
    ev_q = "SELECT count(*) as c FROM evidence" + where
    print(f"Evidence: {conn.execute(ev_q, params).fetchone()['c']}")
    ho_q = "SELECT count(*) as c FROM handovers" + where
    print(f"Handovers: {conn.execute(ho_q, params).fetchone()['c']}")
    ev2_q = "SELECT count(*) as c FROM events" + where
    print(f"Events: {conn.execute(ev2_q, params).fetchone()['c']}")
    md = db_path.parent / "exports" / "latest-handover.md"
    if md.exists():
        print(f"Latest handover: {md}")
        print(md.read_text(encoding="utf-8")[:800])
    conn.close()
    return 0


def cmd_status_compat(args: argparse.Namespace) -> int:
    return cmd_status(args)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="brain", description="Project Brain CLI — cross-project brain management")
    p.add_argument("--db", help="path to .brain/brain.db (default: auto-detect .brain/ upwards)")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init", help="initialize a brain in target project")
    s.add_argument("--project", help="project_id (default: directory name)")
    s.add_argument("--dir", help="target project directory (default: cwd)")
    s.add_argument("--purpose", help="identity purpose for seed")
    s.add_argument("--seed", action="store_true", help="write minimal identity/state/task seed")
    s.set_defaults(func=cmd_init)

    s = sub.add_parser("onboard", help="get onboarding brief for a new agent")
    s.add_argument("--project", help="project_id (default: from .brain/config.json)")
    s.add_argument("--agent", required=True, help="agent_id")
    s.add_argument("--session", help="session_id")
    s.add_argument("--focus", help="focus topic for contextual brief")
    s.add_argument("--token-budget", type=int, default=1800)
    s.set_defaults(func=cmd_onboard)

    s = sub.add_parser("ask", help="ask brain with natural language")
    s.add_argument("--project", help="project_id")
    s.add_argument("--agent", required=True)
    s.add_argument("--session", help="session_id")
    s.add_argument("--question", required=True, help="question text")
    s.add_argument("--scope", help="comma-separated scope filter, e.g. 'uart,dma'")
    s.add_argument("--limit", type=int, default=8)
    s.add_argument("--no-evidence", action="store_true")
    s.set_defaults(func=cmd_ask)

    s = sub.add_parser("record", help="record knowledge/experience/decision/task/evidence/event")
    s.add_argument("--project", help="project_id")
    s.add_argument("--agent", required=True)
    s.add_argument("--session", help="session_id")
    s.add_argument("--type", dest="type", help="record type: identity/state/knowledge/experience/decision/task/evidence/event")
    s.add_argument("--content", help="JSON string or plain text content")
    s.add_argument("--status", help="status, e.g. active/verified/proposed")
    s.add_argument("--task-status", help="task_status for tasks: draft/in_progress/blocked/completed/cancelled")
    s.add_argument("--tags", help="comma-separated tags")
    s.add_argument("--evidence", help="JSON array of evidence objects")
    s.add_argument("--evidence-ids", help="comma-separated evidence ids to link")
    s.add_argument("--confidence", type=float, help="confidence 0-1")
    s.add_argument("--file", help="JSON file containing records array")
    s.set_defaults(func=cmd_record)

    s = sub.add_parser("verify", help="verify or invalidate a memory")
    s.add_argument("--project", help="project_id")
    s.add_argument("--id", required=True, help="memory id, e.g. D-001")
    s.add_argument("--action", required=True, choices=["verify", "invalidate"])
    s.set_defaults(func=cmd_verify)

    s = sub.add_parser("link", help="create a relation between two ids")
    s.add_argument("--project", help="project_id")
    s.add_argument("--from-id", required=True)
    s.add_argument("--relation", required=True, choices=["supports", "supersedes", "conflicts_with", "related_to", "evidence_of"])
    s.add_argument("--to-id", required=True)
    s.set_defaults(func=cmd_link)

    s = sub.add_parser("export", help="export brain data as JSON")
    s.add_argument("--project", help="project_id filter")
    s.add_argument("--what", default="all", choices=["all", "memories", "evidence", "links", "events", "handovers"])
    s.add_argument("--out", help="output file path (default: stdout)")
    s.set_defaults(func=cmd_export)

    s = sub.add_parser("handover", help="create a handover report")
    s.add_argument("--project", help="project_id")
    s.add_argument("--agent", required=True)
    s.add_argument("--session", help="session_id")
    s.add_argument("--task", help="task_id, e.g. T-001")
    s.add_argument("--status", required=True, choices=["completed", "partial", "failed"])
    s.add_argument("--completed", help='JSON array or comma-separated, e.g. \'["done1","done2"]\'')
    s.add_argument("--failed", help="JSON array or comma-separated")
    s.add_argument("--discovered", help="JSON array or comma-separated")
    s.add_argument("--remaining", help="JSON array or comma-separated")
    s.add_argument("--next-step", help="recommended next step text")
    s.add_argument("--evidence-ids", help="comma-separated evidence ids")
    s.set_defaults(func=cmd_handover)

    s = sub.add_parser("status", help="show brain overview")
    s.add_argument("--project", help="project_id filter")
    s.set_defaults(func=cmd_status)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
