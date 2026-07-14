"""GET /api/v1/version. Phase D addition (A1.2). F-1 transparency.

Returns `{agibridge_git_sha, embodied_data_version, built_at}`:
- `agibridge_git_sha` from env `GIT_SHA` (set by Dockerfile `ARG GIT_SHA`).
- `embodied_data_version` from `importlib.metadata.version` (Phase D C1).
- `built_at` from env `BUILT_AT` (Dockerfile sets at build time, ISO 8601).

Local dev fallback: read `git rev-parse HEAD` if env not present.
"""

from __future__ import annotations

import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Request, Response

from app.api.health import get_embodied_data_version
from app.api.rate_limit import HEALTH_VERSION_LIMIT, limiter
from app.schemas import VersionResponse

router = APIRouter()


def _git_sha() -> str:
    """Prefer env (set in Docker), fall back to `git rev-parse HEAD` for dev."""
    if env := os.environ.get("GIT_SHA"):
        return env
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=2,
            cwd=Path(__file__).resolve().parent.parent.parent,
            check=False,
        )
        if out.returncode == 0:
            return out.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return "unknown"


def _built_at() -> str:
    """Prefer env (set in Docker), fall back to "dev" for local."""
    if env := os.environ.get("BUILT_AT"):
        return env
    return datetime.now(UTC).isoformat() + " (dev)"


@router.get("/version", response_model=VersionResponse)
@limiter.limit(HEALTH_VERSION_LIMIT)
async def version(request: Request, response: Response) -> VersionResponse:
    return VersionResponse(
        agibridge_git_sha=_git_sha(),
        embodied_data_version=get_embodied_data_version(),
        built_at=_built_at(),
    )
