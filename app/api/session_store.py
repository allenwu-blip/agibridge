"""In-memory + on-disk session registry. Single uvicorn worker per spec §3.

Each session lives under `/tmp/<ulid>/` with `status.json` as the durable state
record. The in-memory dict is a cache so polling reads are p50<10ms; on
container restart `/tmp` is wiped and the dict starts empty (spec §4 step 2 of
the trigger list).
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.schemas import State

# Spec §4: 30-minute purge window.
PURGE_TIMEOUT = timedelta(minutes=30)
# Spec §6: 25-min subprocess wall clock; 30 min purge; 60 min hard cap.
HARD_PURGE_TIMEOUT = timedelta(minutes=60)


def _root_dir() -> Path:
    """Override-able root for tests (TMPDIR/AGIBRIDGE_ROOT). Default /tmp."""
    if env := os.environ.get("AGIBRIDGE_ROOT"):
        return Path(env)
    return Path(tempfile.gettempdir())


@dataclass
class Session:
    session_id: str
    root: Path
    state: State = State.pending
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    started_at: datetime | None = None
    finished_at: datetime | None = None
    expires_at: datetime = field(default_factory=lambda: datetime.now(UTC) + PURGE_TIMEOUT)
    error_code: str | None = None
    error_message: str | None = None
    error_suggestion: str | None = None
    stderr_tail: str | None = None
    from_format: str | None = None
    to_format: str | None = None
    # Used for estimated_progress_pct (spec §2.2.1).
    estimated_total_s: int | None = None

    @property
    def in_dir(self) -> Path:
        return self.root / "in"

    @property
    def out_dir(self) -> Path:
        return self.root / "out"

    @property
    def result_zip(self) -> Path:
        return self.root / "result.zip"

    @property
    def status_file(self) -> Path:
        return self.root / "status.json"

    def to_status_json(self) -> dict[str, Any]:
        """Serialize for status.json on disk."""
        return {
            "session_id": self.session_id,
            "state": self.state.value,
            "created_at": self.created_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "expires_at": self.expires_at.isoformat(),
            "error_code": self.error_code,
            "error_message": self.error_message,
            "error_suggestion": self.error_suggestion,
            "stderr_tail": self.stderr_tail,
            "from_format": self.from_format,
            "to_format": self.to_format,
            "estimated_total_s": self.estimated_total_s,
        }

    def write_status(self) -> None:
        # Atomic write: tmp + replace, so polling never sees a partial file.
        tmp = self.status_file.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.to_status_json(), default=str))
        tmp.replace(self.status_file)


class SessionStore:
    """Thread-/coroutine-safe session registry. asyncio.Lock guards mutation
    of the GlobalLock acquisition (spec §3 single-flight)."""

    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}
        self._global_lock = asyncio.Lock()
        self._dict_lock = asyncio.Lock()

    @property
    def global_lock(self) -> asyncio.Lock:
        """The conversion single-flight lock. Held for the entire run."""
        return self._global_lock

    async def create(self, session_id: str) -> Session:
        async with self._dict_lock:
            root = _root_dir() / session_id
            root.mkdir(parents=True, exist_ok=False)
            (root / "in").mkdir()
            (root / "out").mkdir()
            sess = Session(session_id=session_id, root=root)
            sess.write_status()
            self._sessions[session_id] = sess
            return sess

    async def get(self, session_id: str) -> Session | None:
        async with self._dict_lock:
            return self._sessions.get(session_id)

    async def list_all(self) -> list[Session]:
        async with self._dict_lock:
            return list(self._sessions.values())

    async def remove(self, session_id: str) -> None:
        async with self._dict_lock:
            self._sessions.pop(session_id, None)

    def active_session(self) -> str | None:
        """Best-effort: return the id of a running session if any. No lock —
        used by /health which doesn't need atomicity."""
        for sid, sess in self._sessions.items():
            if sess.state in (State.pending, State.extracting, State.running, State.validating):
                return sid
        return None
