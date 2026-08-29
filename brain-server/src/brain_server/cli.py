from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .db import DEFAULT_DB_PATH, get_connection, init_db
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


def resolve_agent_id(explicit: str | None, db_path: Path | None = None) -> str:
    """智能推断 agent_id，优先级：显式参数 > 环境变量 > 配置文件 > 默认值"""
    if explicit:
        return explicit
    
    # 尝试从环境变量读取
    import os
    env_agent = os.environ.get("BRAIN_AGENT_ID")
    if env_agent:
        return env_agent
    
    # 尝试从最近的 handover 或 session 推断
    if db_path and db_path.exists():
        try:
            from .db import get_connection
            conn = get_connection(str(db_path))
            # 查询最近的 handover 中的 agent_id
            recent_handover = conn.execute(
                "SELECT agent_id FROM handovers ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
            if recent_handover:
                conn.close()
                return recent_handover["agent_id"]
            conn.close()
        except Exception:
            pass
    
    # 默认值
    return "cli-user"


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
    agent_id = resolve_agent_id(args.agent, db_path)
    req = BrainOnboardRequest(project_id=project_id, agent_id=agent_id, session_id=args.session, focus=args.focus, token_budget=args.token_budget)
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
    agent_id = resolve_agent_id(args.agent, db_path)
    scope = [s.strip() for s in args.scope.split(",") if s.strip()] if args.scope else None
    req = BrainAskRequest(project_id=project_id, agent_id=agent_id, session_id=args.session, question=args.question, scope=scope, include_evidence=not args.no_evidence, limit=args.limit, include_proposals=bool(getattr(args, "include_proposals", False)), as_of_commit=getattr(args, "as_of_commit", None), as_of_time=getattr(args, "as_of_time", None))
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
    agent_id = resolve_agent_id(args.agent, db_path)

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
        records.append(RecordInput(type=args.type, content=content, status=args.status, task_status=getattr(args, "task_status", None), confidence=args.confidence, tags=tags, evidence=evidence, evidence_ids=evidence_ids, origin=getattr(args, "origin", None), valid_from=getattr(args, "valid_from", None), valid_until=getattr(args, "valid_until", None), branch=getattr(args, "branch", None), commit_hash=getattr(args, "commit_hash", None), verification_due_at=getattr(args, "verification_due_at", None)))

    req = BrainRecordRequest(project_id=project_id, agent_id=agent_id, session_id=args.session, records=records)
    resp = brain_record(req, db_path=str(db_path))
    print(json.dumps(resp.model_dump(), ensure_ascii=False, indent=2))
    return 0


def cmd_ingest(args: argparse.Namespace) -> int:
    from .protocol import brain_ingest
    from .models import IngestRequest

    db_path = resolve_db(args.db)
    project_id = resolve_project_id(db_path, args.project)
    if not project_id:
        print("error: --project is required", file=sys.stderr)
        return 2
    if getattr(args, "dry_run", False):
        print(json.dumps({"dry_run": True, "source": args.source, "payload": {"command": getattr(args, "command", None), "log_path": getattr(args, "log_path", None), "path": getattr(args, "path", None)}}, ensure_ascii=False, indent=2))
        return 0
    req = IngestRequest(project_id=project_id, source=args.source, agent_id=getattr(args, "agent", "system"), session_id=getattr(args, "session", None), payload={"command": getattr(args, "command", None), "log_path": getattr(args, "log_path", None), "path": getattr(args, "path", None), "cwd": getattr(args, "cwd", None)})
    resp = brain_ingest(req, db_path=str(db_path))
    print(json.dumps(resp, ensure_ascii=False, indent=2))
    return 0


def cmd_curate(args: argparse.Namespace) -> int:
    from .protocol import brain_curate
    from .models import BrainCurateRequest

    db_path = resolve_db(args.db)
    project_id = resolve_project_id(db_path, args.project)
    if not project_id:
        print("error: --project is required", file=sys.stderr)
        return 2
    event_ids = [s.strip() for s in args.event_ids.split(",") if s.strip()] if getattr(args, "event_ids", None) else None
    req = BrainCurateRequest(project_id=project_id, agent_id=getattr(args, "agent", None), session_id=getattr(args, "session", None), event_ids=event_ids, mode=getattr(args, "mode", "rule"))
    resp = brain_curate(req, db_path=str(db_path))
    print(json.dumps(resp.model_dump(), ensure_ascii=False, indent=2))
    return 0


def cmd_review_list(args: argparse.Namespace) -> int:
    from .protocol import brain_review_list
    from .models import BrainReviewListRequest

    db_path = resolve_db(args.db)
    project_id = resolve_project_id(db_path, args.project)
    if not project_id:
        print("error: --project is required", file=sys.stderr)
        return 2
    req = BrainReviewListRequest(project_id=project_id, status=getattr(args, "status", None), limit=getattr(args, "limit", 20))
    resp = brain_review_list(req, db_path=str(db_path))
    print(json.dumps(resp.model_dump(), ensure_ascii=False, indent=2))
    return 0


def cmd_review_apply(args: argparse.Namespace) -> int:
    from .protocol import brain_review_apply
    from .models import BrainReviewApplyRequest

    db_path = resolve_db(args.db)
    project_id = resolve_project_id(db_path, args.project)
    if not project_id:
        print("error: --project is required", file=sys.stderr)
        return 2
    actions = [a.strip() for a in args.action.split(",")] if "," in args.action else [args.action]
    pids = [p.strip() for p in args.proposal_id.split(",") if p.strip()]
    if len(actions) == 1 and len(pids) > 1:
        actions = actions * len(pids)
    if len(actions) != len(pids):
        print("error: --action count must match --proposal-id count", file=sys.stderr)
        return 2
    last = None
    for pid, act in zip(pids, actions):
        req = BrainReviewApplyRequest(project_id=project_id, proposal_id=pid, action=act, reviewer=args.reviewer, reason=getattr(args, "reason", None))  # type: ignore[arg-type]
        try:
            resp = brain_review_apply(req, db_path=str(db_path))
            print(json.dumps(resp.model_dump(), ensure_ascii=False, indent=2))
            last = resp
        except Exception as e:
            print(json.dumps({"proposal_id": pid, "error": str(e)}, ensure_ascii=False))
            return 1
    return 0


def cmd_snapshot(args: argparse.Namespace) -> int:
    from .protocol import brain_snapshot, brain_rebuild_snapshot
    from .models import BrainSnapshotRequest

    db_path = resolve_db(args.db)
    project_id = resolve_project_id(db_path, args.project)
    if not project_id:
        print("error: --project is required", file=sys.stderr)
        return 2
    if getattr(args, "rebuild", None):
        resp = brain_rebuild_snapshot(args.rebuild, project_id, db_path=str(db_path))
        print(json.dumps(resp.model_dump(), ensure_ascii=False, indent=2))
        return 0
    req = BrainSnapshotRequest(project_id=project_id, basis_commit=getattr(args, "commit", None), basis_branch=getattr(args, "branch", None))
    resp = brain_snapshot(req, db_path=str(db_path))
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
    agent_id = resolve_agent_id(args.agent, db_path)

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
        agent_id=agent_id,
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


def cmd_doctor(args: argparse.Namespace) -> int:
    db_path = resolve_db(args.db)
    if not db_path.exists():
        print(f"no brain at {db_path} — run `brain init` first", file=sys.stderr)
        return 1

    from .db import get_connection
    from .repository import check_evidence_health

    project_id = resolve_project_id(db_path, getattr(args, "project", None)) or "default"
    conn = get_connection(str(db_path))

    try:
        health = check_evidence_health(conn, project_id)
        reachable = [h for h in health if h["health"] == "reachable"]
        moved = [h for h in health if h["health"] == "moved"]
        missing = [h for h in health if h["health"] == "missing"]
        external = [h for h in health if h["health"] == "external"]
        unknown = [h for h in health if h["health"] == "unknown"]

        print(f"Project: {project_id}")
        print(f"Total evidence: {len(health)}")
        print(f"  Reachable: {len(reachable)}")
        print(f"  Moved: {len(moved)}")
        print(f"  Missing: {len(missing)}")
        print(f"  External: {len(external)}")
        print(f"  Unknown: {len(unknown)}")
        print()

        if moved:
            print("Moved evidence (may need path fix):")
            for h in moved[:10]:
                print(f"  {h['id']}: {h['source']} -> {h.get('path', 'N/A')}")
            if len(moved) > 10:
                print(f"  ... and {len(moved) - 10} more")
            print()

        if missing:
            print("Missing evidence:")
            for h in missing[:10]:
                print(f"  {h['id']}: {h['source']}")
            if len(missing) > 10:
                print(f"  ... and {len(missing) - 10} more")
            print()

        if not moved and not missing:
            print("All evidence paths are valid.")

        if getattr(args, "detail", False):
            try:
                from .health import brain_health

                bh = brain_health(conn, project_id)
                print("Brain Health")
                print(f"├── Evidence health: {bh['evidence']['reachable']}/{bh['evidence']['total']} reachable")
                mem_w = bh["memory"]["warnings"]
                print(f"├── Memory health: {len(mem_w)} warnings")
                for w in mem_w[:5]:
                    print(f"│   - {w['kind']}: {w.get('id', w.get('ids',''))} {w.get('detail','')}")
                print(f"├── Provenance coverage: {bh['provenance_coverage']}")
                print(f"└── Workflow: {bh['workflow']['active_sessions']} active sessions")
            except Exception as e:
                print(f"(health detail unavailable: {e})")

        if not moved and not missing and not getattr(args, "detail", False):
            return 0

        if getattr(args, "fix_paths", False):
            print("Path fixing requires manual review. Use relative locator_type for better portability.")
            return 1

        return 0
    finally:
        conn.close()


def cmd_backup(args: argparse.Namespace) -> int:
    db_path = resolve_db(args.db)
    if not db_path.exists():
        print(f"no brain at {db_path} — run `brain init` first", file=sys.stderr)
        return 1

    import shutil
    from datetime import datetime, timezone

    target_dir = args.output or db_path.parent / "backups"
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    backup_dir = Path(target_dir) / f"backup-{timestamp}"
    backup_dir.mkdir(parents=True, exist_ok=True)

    # Copy database — checkpoint WAL first and use backup API for hot backup safety
    db_dest = backup_dir / "brain.db"
    try:
        import sqlite3 as _sqlite3
        src = _sqlite3.connect(str(db_path), timeout=30.0)
        src.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        src.commit()
        src.close()
    except Exception:
        pass
    try:
        import sqlite3 as _sqlite3
        src = _sqlite3.connect(str(db_path), timeout=30.0)
        dst = _sqlite3.connect(str(db_dest), timeout=30.0)
        src.backup(dst)
        dst.close()
        src.close()
    except Exception:
        shutil.copy2(db_path, db_dest)

    # Copy config
    cfg_src = db_path.parent / "config.json"
    if cfg_src.exists():
        shutil.copy2(cfg_src, backup_dir / "config.json")

    # Copy exports
    exports_src = db_path.parent / "exports"
    if exports_src.exists():
        exports_dest = backup_dir / "exports"
        exports_dest.mkdir()
        for f in exports_src.iterdir():
            if f.is_file():
                shutil.copy2(f, exports_dest / f.name)

    # Create manifest
    import hashlib
    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "db_path": str(db_path),
        "backup_dir": str(backup_dir),
        "database_checksum": hashlib.sha256(db_dest.read_bytes()).hexdigest(),
    }
    (backup_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False))

    print(f"Backup created: {backup_dir}")
    print(f"Database: {db_dest}")
    print(f"Manifest: {backup_dir / 'manifest.json'}")
    return 0


def cmd_restore(args: argparse.Namespace) -> int:
    import shutil

    backup_path = Path(args.input)
    if not backup_path.exists():
        print(f"backup not found: {backup_path}", file=sys.stderr)
        return 1

    manifest_path = backup_path / "manifest.json"
    if not manifest_path.exists():
        print(f"no manifest found in {backup_path}", file=sys.stderr)
        return 1

    # Validate before restore
    manifest = json.loads(manifest_path.read_text())
    db_src = backup_path / "brain.db"
    if not db_src.exists():
        print(f"database file missing in backup", file=sys.stderr)
        return 1

    # Check integrity by opening
    import sqlite3
    try:
        conn = sqlite3.connect(str(db_src))
        conn.execute("PRAGMA integrity_check")
        conn.close()
    except Exception as e:
        print(f"database integrity check failed: {e}", file=sys.stderr)
        return 1

    # Restore
    target_db = resolve_db(args.db)
    target_db.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(db_src, target_db)

    cfg_src = backup_path / "config.json"
    if cfg_src.exists():
        cfg_dest = target_db.parent / "config.json"
        shutil.copy2(cfg_src, cfg_dest)

    exports_src = backup_path / "exports"
    if exports_src.exists():
        exports_dest = target_db.parent / "exports"
        exports_dest.mkdir(parents=True, exist_ok=True)
        for f in exports_src.iterdir():
            if f.is_file():
                shutil.copy2(f, exports_dest / f.name)

    print(f"Restored from {backup_path} to {target_db}")
    return 0


def _inferred_agent_id(db_path, explicit):
    if explicit:
        return explicit
    try:
        from .db import get_connection as _gc
        conn = _gc(str(db_path))
        c = conn.execute("SELECT agent_id FROM handovers ORDER BY created_at DESC LIMIT 1").fetchone()
        if c and c["agent_id"]:
            conn.close()
            return c["agent_id"]
        conn.close()
    except Exception:
        pass
    return "cli-user"

def cmd_workflow(args: argparse.Namespace) -> int:
    """Handle workflow automation commands."""
    from .workflow import WorkflowBrain
    from .workflow_models import WorkflowConfig

    db_path = resolve_db(args.db)
    project_id = resolve_project_id(db_path, args.project)
    if not project_id:
        print("error: --project is required (no config found)", file=sys.stderr)
        return 2

    config = WorkflowConfig(project_id=project_id)
    wb = WorkflowBrain(db_path=str(db_path), config=config)

    action = getattr(args, "action", None)
    session_id = getattr(args, "session_id", None)
    agent_id = getattr(args, "agent", "cli-user")
    level = getattr(args, "level", "compact")

    if action == "start":
        start = wb.start_session(
            project_id=project_id,
            agent_id=agent_id,
            level=level,
            session_id=session_id,
        )
        print(json.dumps({
            "session_id": start.session_id,
            "context": start.context,
            "basis_commit": start.basis_commit,
            "warnings": start.warnings,
        }, ensure_ascii=False, indent=2))
        return 0

    elif action == "observe":
        # Read observation from stdin or args
        import sys
        if sys.stdin.isatty():
            print("Enter observation JSON (Ctrl+D to send):", file=sys.stderr)
            data = sys.stdin.read()
        else:
            data = sys.stdin.read()
        try:
            obs = json.loads(data)
        except json.JSONDecodeError as e:
            print(f"error: invalid JSON: {e}", file=sys.stderr)
            return 2
        receipt = wb.observe(
            project_id=project_id,
            observation=obs,
            session_id=session_id or "default",
        )
        print(json.dumps({
            "observation_id": receipt.observation_id,
            "event_ids": receipt.event_ids,
            "evidence_ids": receipt.evidence_ids,
            "warnings": receipt.warnings,
        }, ensure_ascii=False, indent=2))
        return 0

    elif action == "end":
        draft = wb.end_session(
            project_id=project_id,
            session_id=session_id or "default",
        )
        print(json.dumps({
            "draft_id": draft.draft_id,
            "status": draft.status,
            "report": draft.report,
            "source_event_ids": draft.source_event_ids,
        }, ensure_ascii=False, indent=2))
        return 0

    elif action == "status":
        # Show current sessions
        from .repository import list_sessions
        conn = get_connection(str(db_path))
        sessions = list_sessions(conn, project_id=project_id, limit=10)
        conn.close()
        print(json.dumps(sessions, ensure_ascii=False, indent=2))
        return 0

    else:
        print("error: --action required (start|observe|end|status)", file=sys.stderr)
        return 2


def cmd_feedback(args: argparse.Namespace) -> int:
    from .models import BrainFeedbackRequest
    from .protocol import brain_feedback

    db_path = resolve_db(args.db)
    project_id = resolve_project_id(db_path, args.project)
    if not project_id:
        print("error: --project is required", file=sys.stderr)
        return 2
    agent_id = resolve_agent_id(args.agent, db_path)
    req = BrainFeedbackRequest(
        project_id=project_id,
        agent_id=agent_id,
        session_id=getattr(args, "session", None),
        question=args.question,
        verdict=args.verdict,  # type: ignore[arg-type]
        corrected_text=getattr(args, "corrected_text", None),
        intent=getattr(args, "intent", None),
    )
    resp = brain_feedback(req, db_path=str(db_path))
    print(json.dumps(resp.model_dump(), ensure_ascii=False, indent=2))
    return 0


def cmd_capabilities(args: argparse.Namespace) -> int:
    db_path = resolve_db(args.db)
    capabilities = {
        "schema_version": "4",
        "brain_version": "0.4",
        "features": {
            "fts_search": True,
            "project_isolation": True,
            "evidence_locator": True,
            "optimistic_locking": True,
            "audit_events": True,
            "model_curator": True,
            "rule_curator": True,
            "handover": True,
            "snapshot": True,
            "answer_claims": True,
            "feedback": True,
            "clustering": True,
            "clarification": True,
            "memory_health": True,
        },
        "providers": {
            "search": "fts5",
        },
        "git_available": False,
    }

    # Check git availability
    try:
        import subprocess
        subprocess.run(["git", "--version"], capture_output=True, timeout=2)
        capabilities["git_available"] = True
    except Exception:
        pass

    # Read schema version from DB if exists
    if db_path.exists():
        try:
            from .db import get_connection
            conn = get_connection(str(db_path))
            cur = conn.execute("SELECT v FROM schema_meta WHERE k='version'")
            row = cur.fetchone()
            if row:
                capabilities["schema_version"] = row["v"]
            conn.close()
        except Exception:
            pass

    print(json.dumps(capabilities, indent=2, ensure_ascii=False))
    return 0


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
    s.add_argument("--agent", help="agent_id (default: auto-inferred from recent handover or 'cli-user')")
    s.add_argument("--session", help="session_id")
    s.add_argument("--focus", help="focus topic for contextual brief")
    s.add_argument("--token-budget", type=int, default=1800)
    s.set_defaults(func=cmd_onboard)

    s = sub.add_parser("ask", help="ask brain with natural language")
    s.add_argument("--project", help="project_id")
    s.add_argument("--agent", help="agent_id (default: auto-inferred from recent handover or 'cli-user')")
    s.add_argument("--session", help="session_id")
    s.add_argument("--question", required=True, help="question text")
    s.add_argument("--scope", help="comma-separated scope filter, e.g. 'uart,dma'")
    s.add_argument("--limit", type=int, default=8)
    s.add_argument("--no-evidence", action="store_true")
    s.add_argument("--include-proposals", action="store_true")
    s.add_argument("--as-of-commit", help="filter by commit_hash")
    s.add_argument("--as-of-time", help="ISO time for valid window")
    s.set_defaults(func=cmd_ask)

    s = sub.add_parser("record", help="record knowledge/experience/decision/task/evidence/event")
    s.add_argument("--project", help="project_id")
    s.add_argument("--agent", help="agent_id (default: auto-inferred from recent handover or 'cli-user')")
    s.add_argument("--session", help="session_id")
    s.add_argument("--type", dest="type", help="record type: identity/state/knowledge/experience/decision/task/evidence/event")
    s.add_argument("--content", help="JSON string or plain text content")
    s.add_argument("--status", help="status, e.g. active/verified/proposed")
    s.add_argument("--task-status", help="task_status for tasks: draft/in_progress/blocked/completed/cancelled")
    s.add_argument("--tags", help="comma-separated tags")
    s.add_argument("--evidence", help="JSON array of evidence objects")
    s.add_argument("--evidence-ids", help="comma-separated evidence ids to link")
    s.add_argument("--confidence", type=float, help="confidence 0-1")
    s.add_argument("--origin", help="origin: user/agent/rule_curator/model_curator/importer")
    s.add_argument("--valid-from", help="ISO time")
    s.add_argument("--valid-until", help="ISO time")
    s.add_argument("--branch", help="branch name")
    s.add_argument("--commit-hash", help="commit hash")
    s.add_argument("--verification-due-at", help="ISO time")
    s.add_argument("--file", help="JSON file containing records array")
    s.set_defaults(func=cmd_record)

    s = sub.add_parser("ingest", help="ingest git/test/file event")
    s.add_argument("--project", help="project_id")
    s.add_argument("--source", required=True, choices=["git", "test", "file", "handover", "record"])
    s.add_argument("--agent", default="system")
    s.add_argument("--session", help="session_id")
    s.add_argument("--command", help="test command for source=test")
    s.add_argument("--log-path", help="log path for test evidence")
    s.add_argument("--path", help="file path for source=file")
    s.add_argument("--cwd", help="working dir for collectors")
    s.add_argument("--dry-run", action="store_true")
    s.set_defaults(func=cmd_ingest)

    s = sub.add_parser("curate", help="run curator to generate proposals")
    s.add_argument("--project", help="project_id")
    s.add_argument("--agent", help="agent_id")
    s.add_argument("--session", help="session_id")
    s.add_argument("--event-ids", help="comma-separated event ids (default: recent)")
    s.add_argument("--mode", default="rule", choices=["rule", "model", "auto"])
    s.set_defaults(func=cmd_curate)

    s = sub.add_parser("review", help="list or apply proposals")
    s.add_argument("--project", help="project_id")
    s.add_argument("--status", help="pending/approved/rejected/deferred/superseded")
    s.add_argument("--limit", type=int, default=20)
    s.add_argument("--proposal-id", help="proposal id(s) for apply, comma-separated")
    s.add_argument("--action", help="approved/rejected/deferred/superseded")
    s.add_argument("--reviewer", help="reviewer id")
    s.add_argument("--reason", help="reason")
    s.set_defaults(func=cmd_review_list)
    s = sub.add_parser("review-list", help="list proposals")
    s.add_argument("--project", help="project_id")
    s.add_argument("--status", help="pending/approved/rejected/deferred/superseded")
    s.add_argument("--limit", type=int, default=20)
    s.set_defaults(func=cmd_review_list)
    s = sub.add_parser("review-apply", help="approve/reject/defer proposal(s)")
    s.add_argument("--project", help="project_id")
    s.add_argument("--proposal-id", required=True, help="proposal id(s), comma-separated")
    s.add_argument("--action", required=True, help="approved/rejected/deferred/superseded")
    s.add_argument("--reviewer", required=True)
    s.add_argument("--reason", help="reason")
    s.set_defaults(func=cmd_review_apply)

    s = sub.add_parser("snapshot", help="create or rebuild model snapshot")
    s.add_argument("--project", help="project_id")
    s.add_argument("--commit", help="basis commit")
    s.add_argument("--branch", help="basis branch")
    s.add_argument("--rebuild", help="snapshot id to rebuild")
    s.set_defaults(func=cmd_snapshot)

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
    s.add_argument("--agent", help="agent_id (default: auto-inferred from recent handover or 'cli-user')")
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

    s = sub.add_parser("doctor", help="check evidence health and diagnostics")
    s.add_argument("--project", help="project_id filter")
    s.add_argument("--fix-paths", action="store_true", help="fix relocatable evidence paths (requires confirmation)")
    s.add_argument("--detail", action="store_true", help="show 5-layer brain health")
    s.set_defaults(func=cmd_doctor)

    s = sub.add_parser("feedback", help="submit answer feedback (does not modify Memory)")
    s.add_argument("--project", help="project_id")
    s.add_argument("--agent", help="agent_id")
    s.add_argument("--session", help="session_id")
    s.add_argument("--question", required=True, help="original question")
    s.add_argument("--verdict", required=True, choices=["accepted", "corrected", "expanded", "irrelevant", "missing_evidence"])
    s.add_argument("--corrected-text", help="corrected answer when verdict=corrected")
    s.add_argument("--intent", help="reported intent")
    s.set_defaults(func=cmd_feedback)

    s = sub.add_parser("backup", help="backup brain database")
    s.add_argument("--output", help="output directory for backup")
    s.set_defaults(func=cmd_backup)

    s = sub.add_parser("restore", help="restore brain from backup")
    s.add_argument("--input", required=True, help="backup directory path")
    s.add_argument("--db", help="target database path (default: auto-detect)")
    s.set_defaults(func=cmd_restore)

    s = sub.add_parser("capabilities", help="show available capabilities")
    s.set_defaults(func=cmd_capabilities)

    # v0.5: workflow subcommands
    s = sub.add_parser("workflow", help="workflow automation management")
    s.add_argument("--project", help="project_id")
    s.add_argument("--action", help="start|observe|end|pause|resume|flush|status")
    s.add_argument("--session-id", help="session_id")
    s.add_argument("--agent", help="agent_id")
    s.add_argument("--level", default="compact", choices=["compact", "focused", "full"])
    s.set_defaults(func=cmd_workflow)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
