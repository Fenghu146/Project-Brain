from __future__ import annotations

import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import IngestRequest, now_iso
from .protocol import brain_ingest
from .workflow_buffer import Observation, PrivacyFilter
from .workflow_models import WorkflowConfig


class GitTrigger:
    """Handles Git-related observations."""

    def __init__(self, privacy: PrivacyFilter | None = None):
        self.privacy = privacy or PrivacyFilter()

    def observe_commit(self, cwd: str, session_id: str, project_id: str) -> dict[str, Any]:
        """Observe a Git commit."""
        try:
            commit = subprocess.check_output(
                ["git", "rev-parse", "HEAD"],
                cwd=cwd,
                text=True,
                timeout=5,
            ).strip() or None
            branch = subprocess.check_output(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                cwd=cwd,
                text=True,
                timeout=5,
            ).strip() or None
            changed = subprocess.check_output(
                ["git", "diff", "--name-only", "HEAD"],
                cwd=cwd,
                text=True,
                timeout=5,
            ).strip().splitlines() or []

            obs = Observation(
                kind="git",
                source=commit or "unknown",
                result="observed",
                payload={
                    "commit": commit,
                    "branch": branch,
                    "changed_files": changed[:10],  # Limit to 10 files
                    "cwd": cwd,
                },
                session_id=session_id,
                idempotency_key=f"git:{commit}:{branch}" if commit else None,
            )

            if self.privacy.should_skip(obs):
                return {"skipped": True, "reason": "privacy filter"}

            result = brain_ingest(IngestRequest(
                project_id=project_id,
                source="git",
                agent_id="workflow",
                session_id=session_id,
                payload={"cwd": cwd},
            ))
            return {
                "event_id": result.get("event_id"),
                "duplicate": result.get("duplicate", False),
                "commit": commit,
                "branch": branch,
                "changed_files": len(changed),
            }
        except subprocess.TimeoutExpired:
            return {"error": "git timeout"}
        except Exception as e:
            return {"error": str(e)}


class TestTrigger:
    """Handles test-related observations."""

    def __init__(self, privacy: PrivacyFilter | None = None):
        self.privacy = privacy or PrivacyFilter()
        self._last_run: dict[str, float] = {}  # key -> timestamp for debounce

    def observe_test(self, command: str, cwd: str, session_id: str, project_id: str) -> dict[str, Any]:
        """Observe a test run."""
        debounce_key = f"test:{command}:{cwd}"
        now = time.time()
        if debounce_key in self._last_run:
            if now - self._last_run[debounce_key] < 30:  # 30s debounce
                return {"skipped": True, "reason": "debounce"}
        self._last_run[debounce_key] = now

        try:
            import subprocess
            proc = subprocess.run(
                command,
                shell=True,
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=60,
            )
            result = "passed" if proc.returncode == 0 else "failed"

            obs = Observation(
                kind="test",
                source=command,
                result=result,
                payload={
                    "exit_code": proc.returncode,
                    "stdout": proc.stdout[:500] if proc.stdout else "",
                    "stderr": proc.stderr[:500] if proc.stderr else "",
                    "cwd": cwd,
                },
                session_id=session_id,
            )

            if self.privacy.should_skip(obs):
                return {"skipped": True, "reason": "privacy filter"}

            ing_result = brain_ingest(IngestRequest(
                project_id=project_id,
                source="test",
                agent_id="workflow",
                session_id=session_id,
                payload={"command": command, "cwd": cwd, "log_path": None},
            ))
            return {
                "event_id": ing_result.get("event_id"),
                "result": result,
                "exit_code": proc.returncode,
            }
        except subprocess.TimeoutExpired:
            return {"error": "test timeout", "result": "failed"}
        except Exception as e:
            return {"error": str(e), "result": "failed"}


class FileTrigger:
    """Handles file change observations."""

    def __init__(self, privacy: PrivacyFilter | None = None):
        self.privacy = privacy or PrivacyFilter()

    def observe_file(self, path: str, session_id: str, project_id: str) -> dict[str, Any]:
        """Observe a file change."""
        p = Path(path)
        if not p.exists():
            return {"skipped": True, "reason": "file not found"}

        obs = Observation(
            kind="file",
            source=str(p),
            result="observed",
            payload={
                "path": str(p),
                "exists": p.exists(),
                "is_file": p.is_file(),
            },
            session_id=session_id,
            idempotency_key=f"file:{str(p)}",
        )

        if self.privacy.should_skip(obs):
            return {"skipped": True, "reason": "privacy filter"}

        result = brain_ingest(IngestRequest(
            project_id=project_id,
            source="file",
            agent_id="workflow",
            session_id=session_id,
            payload={"path": str(p)},
        ))
        return {
            "event_id": result.get("event_id"),
            "path": str(p),
        }


def create_triggers(config: WorkflowConfig) -> dict[str, Any]:
    """Create trigger handlers based on config."""
    privacy = PrivacyFilter(
        exclude_paths=config.exclude_paths,
        redact_patterns=config.redact_patterns,
        max_payload_bytes=config.max_payload_bytes,
    )
    return {
        "git": GitTrigger(privacy=privacy),
        "test": TestTrigger(privacy=privacy),
        "file": FileTrigger(privacy=privacy),
    }
