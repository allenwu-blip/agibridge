# DEPRECATED — superseded by app/db/job_store.py (DB-backed, org-scoped).
#
# D4-A Refactor #4: the `SessionStore` class is no longer the source of
# truth and is NOT wired into `app/main.py`'s lifespan. Job state lives in
# Postgres via `JobStore`; dataset bytes live in R2.
#
# The `Session` dataclass + `_root_dir()` are RETAINED ON PURPOSE: the
# verified-working subprocess runner (`subprocess_runner.py`, DR-019)
# consumes a `Session`-shaped scratch workspace (sess.in_dir / sess.out_dir
# / sess.from_format / sess.to_format / sess.root). `app/api/jobs.py`
# reuses that contract as a local scratch dir so the verified subprocess
# invocation stays byte-for-byte identical. Do NOT delete `Session` /
# `_root_dir`; do NOT reintroduce `SessionStore` as a registry.
"""In-memory + on-disk session registry. [DEPRECATED — see header above.]

Single uvicorn worker per spec §3. Each session lived under `/tmp/<ulid>/`
with `status.json` as the durable record. Retained only as the scratch
workspace shape for the verified subprocess runner.
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
    # Tier-gating (Story #8): when set, `subprocess_runner._build_cmd`
    # appends `--max-episodes N` to the embodied-data invocation. None ==
    # unset == full conversion (DR-019 byte-for-byte path is UNCHANGED when
    # this is None; the flag is purely additive). Free tier forces 1 per
    # `dispatches/D4_specs.md:149`.
    max_episodes: int | None = None
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
