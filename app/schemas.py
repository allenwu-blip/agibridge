"""Pydantic request/response models. The shape here drives the generated
``openapi.json`` that frontend-dev consumes — keep it aligned with spec v2.2
sections 2.1, 2.2, 2.2.1, 2.3, 2.4, and Phase D additions (`/api/v1/version`).
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class FromFormat(str, Enum):
    # Per spec §2.1: supported pairs from convert/__init__.py:19-22 (`_SUPPORTED`).
    agibot = "agibot"
    lerobot_v3 = "lerobot-v3"


class ToFormat(str, Enum):
    agibot = "agibot"
    lerobot_v3 = "lerobot-v3"


class State(str, Enum):
    """Conversion state machine. Per spec §2.2 (no episode counters; the lib
    emits exactly one terminal JSON per `--json` run — convert/__init__.py:386,
    502, 574). `validating` is reserved for future granularity; v0 collapses
    convert+validate into `running`.
    """

    pending = "pending"
    extracting = "extracting"
    running = "running"
    validating = "validating"
    done = "done"
    failed = "failed"
    expired = "expired"


class UploadAccepted(BaseModel):
    """Response 202 from POST /api/v1/upload. See spec §2.1."""

    session_id: str = Field(
        ...,
        description="Server-issued ULID. Not secret enough to be unguessable; do not treat as auth.",
    )
    status_url: str
    expires_at: datetime
    note: str = (
        "This run is ephemeral. Files are kept for 30 minutes, then deleted. "
        "No accounts, no storage."
    )


class ErrorBody(BaseModel):
    """Structured error returned when state == 'failed'.

    Per spec §5: `code` is a wrapper-side enum (NOT from the lib — `_emit.py:41`
    only emits `{error, suggestion, exit_code}`, no `error.code` field). The
    `message` and `suggestion` strings are passed through verbatim from the
    lib's emit_error payload when present; otherwise synthesized by the wrapper.
    """

    code: str
    message: str
    suggestion: str | None = None
    stderr_tail: str | None = None


class StatusResponse(BaseModel):
    """GET /api/v1/status/{session_id}. See spec §2.2 + §2.2.1.

    `estimated_progress_pct` is a wrapper-side estimate, not the lib's actual
    progress (the lib does not expose per-episode progress in --json mode per
    convert/__init__.py:38-43). Frontend MUST render with explicit "(estimate)"
    annotation.
    """

    session_id: str
    state: State
    started_at: datetime | None = None
    finished_at: datetime | None = None
    expires_at: datetime
    error: ErrorBody | None = None
    download_url: str | None = None
    # §2.2.1 v2.2 addition. int 0-99 while running/validating; null otherwise.
    # Clamped at 99; only the `done` state should imply 100% to the user.
    estimated_progress_pct: int | None = Field(default=None, ge=0, le=99)


class HealthResponse(BaseModel):
    """GET /api/v1/health. See spec §2.4 v2.2 (importlib.metadata, not subprocess)."""

    ok: bool = True
    embodied_data_version: str
    uptime_s: int
    active_session: str | None = None
    free_disk_mb: int


class VersionResponse(BaseModel):
    """GET /api/v1/version. Phase D addition (A1.2). F-1 transparency.

    `agibridge_git_sha` from `git rev-parse HEAD` at container build (passed via
    Dockerfile `ARG GIT_SHA`); `embodied_data_version` from
    `importlib.metadata.version('embodied-data')`; `built_at` ISO 8601.
    """

    agibridge_git_sha: str
    embodied_data_version: str
    built_at: str


class ApiError(BaseModel):
    """Generic non-conversion error (4xx). Maps to FastAPI's `detail` shape but
    typed so OpenAPI shows the structure to frontend."""

    code: str
    message: str
    suggestion: str | None = None
