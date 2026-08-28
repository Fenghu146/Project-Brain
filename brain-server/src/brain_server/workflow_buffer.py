from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class Observation:
    kind: str  # git|test|file|agent_note|command
    source: str
    result: str = "observed"
    payload: dict[str, Any] = field(default_factory=dict)
    session_id: str | None = None
    idempotency_key: str | None = None
    timestamp: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "source": self.source,
            "result": self.result,
            "payload": self.payload,
            "session_id": self.session_id,
            "idempotency_key": self.idempotency_key,
            "timestamp": self.timestamp or datetime.now(timezone.utc).isoformat(),
        }


class SessionBuffer:
    """In-memory buffer for observations, persists to disk on flush."""

    def __init__(
        self,
        max_items: int = 1000,
        ttl_minutes: int = 30,
        storage_path: str | None = None,
    ):
        self.max_items = max_items
        self.ttl_seconds = ttl_minutes * 60
        self.storage_path = Path(storage_path) if storage_path else None
        self._buffer: dict[str, dict[str, list[Observation]]] = {}  # project_id -> session_id -> List
        self._timestamps: dict[str, dict[str, float]] = {}  # project_id -> session_id -> last_timestamp

    def add(self, project_id: str, session_id: str, obs: Observation) -> bool:
        """Add observation to buffer. Returns True if buffered, False if discarded."""
        if project_id not in self._buffer:
            self._buffer[project_id] = {}
            self._timestamps[project_id] = {}
        if session_id not in self._buffer[project_id]:
            self._buffer[project_id][session_id] = []
            self._timestamps[project_id][session_id] = time.time()

        buf = self._buffer[project_id][session_id]
        if len(buf) >= self.max_items:
            buf.pop(0)  # Discard oldest
        buf.append(obs)
        self._timestamps[project_id][session_id] = time.time()
        return True

    def get_pending(self, project_id: str, session_id: str) -> list[Observation]:
        """Get buffered observations for a session."""
        if project_id in self._buffer and session_id in self._buffer[project_id]:
            return list(self._buffer[project_id][session_id])
        return []

    def clear(self, project_id: str, session_id: str) -> None:
        """Clear buffer for a session."""
        if project_id in self._buffer:
            self._buffer[project_id].pop(session_id, None)
            self._timestamps[project_id].pop(session_id, None)

    def cleanup_expired(self) -> int:
        """Remove expired entries. Returns count removed."""
        now = time.time()
        removed = 0
        for proj_id, sessions in list(self._buffer.items()):
            for sess_id in list(sessions.keys()):
                ts = self._timestamps.get(proj_id, {}).get(sess_id, 0)
                if now - ts > self.ttl_seconds:
                    self._buffer[proj_id].pop(sess_id, None)
                    self._timestamps.get(proj_id, {}).pop(sess_id, None)
                    removed += 1
        return removed

    def persist(self) -> None:
        """Persist buffer to disk."""
        if not self.storage_path:
            return
        data = {
            "buffer": {
                proj: {sess: [o.to_dict() for o in obs] for sess, obs in sessions.items()}
                for proj, sessions in self._buffer.items()
            },
            "persisted_at": datetime.now(timezone.utc).isoformat(),
        }
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        self.storage_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def load(self) -> None:
        """Load buffer from disk."""
        if not self.storage_path or not self.storage_path.exists():
            return
        try:
            data = json.loads(self.storage_path.read_text())
            for proj_id, sessions in data.get("buffer", {}).items():
                self._buffer[proj_id] = {}
                for sess_id, obs_list in sessions.items():
                    self._buffer[proj_id][sess_id] = [
                        Observation(**o) for o in obs_list
                    ]
        except Exception:
            pass


class PrivacyFilter:
    """Filters sensitive content from observations."""

    def __init__(
        self,
        exclude_paths: list[str] | None = None,
        redact_patterns: list[str] | None = None,
        max_payload_bytes: int = 8192,
    ):
        self.exclude_paths = exclude_paths or []
        self.redact_patterns = [re.compile(p, re.IGNORECASE) for p in (redact_patterns or [])]
        self.max_payload_bytes = max_payload_bytes

    def should_skip(self, observation: Observation) -> bool:
        """Check if observation should be skipped."""
        content = json.dumps(observation.to_dict())
        if len(content.encode()) > self.max_payload_bytes:
            return True
        for pattern in self.exclude_paths:
            if pattern in observation.source or pattern in str(observation.payload):
                return True
        for regex in self.redact_patterns:
            if regex.search(content):
                return True
        return False

    def redact(self, observation: Observation) -> Observation:
        """Redact sensitive content from observation."""
        redacted_payload = {}
        for k, v in observation.payload.items():
            s = json.dumps(v) if not isinstance(v, str) else v
            for regex in self.redact_patterns:
                s = regex.sub("[REDACTED]", s)
            try:
                redacted_payload[k] = json.loads(s)
            except Exception:
                redacted_payload[k] = s
        return Observation(
            kind=observation.kind,
            source=observation.source,
            result=observation.result,
            payload=redacted_payload,
            session_id=observation.session_id,
            idempotency_key=observation.idempotency_key,
        )


# Default privacy filter configuration
DEFAULT_PRIVACY = PrivacyFilter(
    exclude_paths=["**/.env", "**/secrets/**", "**/*.key"],
    redact_patterns=[r"(?i)\btoken\b", r"(?i)\bpassword\b", r"(?i)\bsecret\b", r"(?i)\bapi[_-]?key\b"],
    max_payload_bytes=8192,
)
