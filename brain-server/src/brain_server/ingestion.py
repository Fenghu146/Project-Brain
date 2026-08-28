from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path
from typing import Any

from .db import get_connection, init_db
from . import repository as repo

MAX_DIFF_CHARS = 1200


def _dedup_key(project_id: str, source: str, parts: dict[str, Any]) -> str:
    raw = f"{project_id}|{source}|{parts.get('commit','')}|{parts.get('branch','')}|{parts.get('command','')}|{parts.get('changed_key','')}"
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def _git_info(cwd: Path) -> tuple[str | None, str | None, list[str], str]:
    try:
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=str(cwd), text=True, timeout=5).strip()
    except Exception:
        commit = None
    try:
        branch = subprocess.check_output(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=str(cwd), text=True, timeout=5).strip()
    except Exception:
        branch = None
    changed: list[str] = []
    diff_summary = ""
    try:
        out = subprocess.check_output(["git", "diff", "--name-only", "HEAD"], cwd=str(cwd), text=True, timeout=5)
        changed = [l.strip() for l in out.splitlines() if l.strip()]
        try:
            diff = subprocess.check_output(["git", "diff", "--stat", "HEAD"], cwd=str(cwd), text=True, timeout=5)
            diff_summary = diff.strip()[:MAX_DIFF_CHARS]
        except Exception:
            diff_summary = ""
    except Exception:
        pass
    return commit, branch, changed, diff_summary


def ingest_git(project_id: str, agent_id: str = "system", session_id: str | None = None, cwd: str | Path | None = None, db_path: str | Path | None = None) -> dict[str, Any]:
    cwd_p = Path(cwd).resolve() if cwd else Path.cwd().resolve()
    conn = init_db(db_path)
    warnings: list[str] = []
    try:
        commit, branch, changed, diff_summary = _git_info(cwd_p)
        key = _dedup_key(project_id, "git", {"commit": commit or "", "branch": branch or "", "changed_key": ",".join(sorted(changed))})
        before = len(repo.list_events(conn, project_id=project_id, limit=1000))
        ev_id = repo.create_event(
            conn,
            project_id=project_id,
            action="commit",
            source="git",
            result="observed",
            agent_id=agent_id,
            session_id=session_id,
            target=commit,
            summary=f"git {commit[:7] if commit else 'unknown'} {branch or ''} changed={len(changed)}",
            payload={"commit": commit, "branch": branch, "changed_files": changed, "diff_summary": diff_summary, "cwd": str(cwd_p)},
            dedup_key=key,
        )
        after = len(repo.list_events(conn, project_id=project_id, limit=1000))
        is_dup = after == before or any(e["dedup_key"] == key and e["id"] != ev_id for e in repo.list_events(conn, project_id=project_id, limit=50))
        conn.commit()
        if is_dup and ("duplicate" not in " ".join(warnings)):
            warnings.append("duplicate_event")
        return {"event_id": ev_id, "dedup_key": key, "duplicate": is_dup, "warnings": warnings, "commit": commit, "branch": branch, "changed_files": changed}
    except Exception as e:
        warnings.append(str(e))
        return {"event_id": None, "warnings": warnings}
    finally:
        conn.close()


def ingest_test(project_id: str, command: str = "make test", agent_id: str = "system", session_id: str | None = None, cwd: str | Path | None = None, db_path: str | Path | None = None, log_path: str | None = None) -> dict[str, Any]:
    cwd_p = Path(cwd).resolve() if cwd else Path.cwd().resolve()
    conn = init_db(db_path)
    warnings: list[str] = []
    try:
        result = "passed"
        exit_code = 0
        try:
            proc = subprocess.run(command, shell=True, cwd=str(cwd_p), capture_output=True, text=True, timeout=30)
            exit_code = proc.returncode
            result = "passed" if exit_code == 0 else "failed"
        except subprocess.TimeoutExpired:
            result = "failed"
            exit_code = 124
            warnings.append("test timeout")
        except Exception as e:
            result = "failed"
            warnings.append(f"test error: {e}")

        commit, branch, _, _ = _git_info(cwd_p)
        key = _dedup_key(project_id, "test", {"commit": commit or "", "command": command, "branch": branch or ""})
        before = len(repo.list_events(conn, project_id=project_id, limit=1000))
        ev_id = repo.create_event(
            conn,
            project_id=project_id,
            action="run_test",
            source="test",
            result=result,
            agent_id=agent_id,
            session_id=session_id,
            target=log_path or command,
            summary=f"test {result} exit={exit_code} cmd={command}",
            payload={"command": command, "exit_code": exit_code, "log_path": log_path, "commit": commit, "branch": branch, "cwd": str(cwd_p)},
            dedup_key=key,
        )
        after = len(repo.list_events(conn, project_id=project_id, limit=1000))
        is_dup = after == before
        if is_dup:
            warnings.append("duplicate_event")
        if log_path and exit_code == 0:
            try:
                repo.create_evidence(conn, project_id=project_id, ev_type="test_result", source=log_path, description=f"test {result}: {command}", metadata={"exit_code": exit_code, "commit": commit}, status="observed")
            except Exception as e:
                warnings.append(f"evidence warning: {e}")
        conn.commit()
        return {"event_id": ev_id, "dedup_key": key, "duplicate": is_dup, "warnings": warnings, "result": result, "exit_code": exit_code, "commit": commit, "branch": branch}
    finally:
        conn.close()


def ingest_file(project_id: str, path: str, agent_id: str = "system", session_id: str | None = None, db_path: str | Path | None = None) -> dict[str, Any]:
    p = Path(path).resolve()
    conn = init_db(db_path)
    warnings: list[str] = []
    try:
        key = _dedup_key(project_id, "file", {"changed_key": str(p)})
        ev_id = repo.create_event(
            conn,
            project_id=project_id,
            action="modify_file",
            source="file",
            result="observed",
            agent_id=agent_id,
            session_id=session_id,
            target=str(p),
            summary=f"file {p.name}",
            payload={"path": str(p), "exists": p.exists()},
            dedup_key=key,
        )
        is_dup = False
        # dedup detection: if we returned existing id, count unchanged handled above; file is usually not duplicate
        conn.commit()
        return {"event_id": ev_id, "dedup_key": key, "duplicate": is_dup, "warnings": warnings}
    except Exception as e:
        warnings.append(str(e))
        return {"event_id": None, "warnings": warnings}
    finally:
        conn.close()
