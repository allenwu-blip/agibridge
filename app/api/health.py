"""GET /api/v1/health. Spec §2.4 v2.2.

Uses `importlib.metadata.version('embodied-data')` (Python stdlib) to read
the lib version — NOT subprocess. Per Phase D C1 + spec v2.2 update: subprocess
is too slow for a high-frequency healthcheck endpoint.

At startup we log a single warning if `importlib.metadata.version` disagrees
with `embodied-data --version --json` output (`cli.py:69-75`); a mismatch
would be a packaging bug, not a per-request failure mode.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess
import sys
import time
from importlib import metadata as _md
from pathlib import Path

from fastapi import APIRouter, HTTPException

from app.api.session_store import SessionStore, _root_dir
from app.schemas import HealthResponse

logger = logging.getLogger("agibridge.health")

router = APIRouter()
_session_store: SessionStore | None = None
_started_at_monotonic = time.monotonic()
_version_drift_logged = False


def set_state(store: SessionStore) -> None:
    global _session_store
    _session_store = store


def get_embodied_data_version() -> str:
    """Spec §2.4 v2.2 / Phase D C1. Stdlib lookup, no subprocess."""
    try:
        return _md.version("embodied-data")
    except _md.PackageNotFoundError:
        return "unknown"


async def log_version_drift_once() -> None:
    """Run `embodied-data --version --json` once at startup and warn if it
    disagrees with `importlib.metadata.version`. Done in lifespan; never
    runs per-request."""
    global _version_drift_logged
    if _version_drift_logged:
        return
    _version_drift_logged = True

    importlib_v = get_embodied_data_version()
    if importlib_v == "unknown":
        return  # Can't compare; lib not installed in this env (e.g. tests).

    cmd = "embodied-data" if shutil.which("embodied-data") else None
    try:
        # Run in thread; subprocess.run is sync. cli.py:69-75 is the version source.
        # HP-7: use sys.executable rather than literal "python" because macOS
        # dev shells often have only `python3` (no bare `python`); CI / venv
        # environments may also differ. Same reasoning as
        # subprocess_runner.py:_build_cmd.
        if cmd is None:
            argv = [sys.executable, "-m", "embodied_data.cli", "--version", "--json"]
        else:
            argv = [cmd, "--version", "--json"]
        result = await asyncio.to_thread(
            subprocess.run, argv, capture_output=True, text=True, timeout=10
        )
        if result.returncode != 0:
            return
        # cli.py:73 emits {"version": ..., "git_sha": ..., "build_date": ...}.
        last_line = result.stdout.strip().splitlines()[-1]
        payload = json.loads(last_line)
        cli_v = payload.get("version", "")
        if cli_v and cli_v != importlib_v:
            logger.warning(
                "embodied-data version drift: importlib.metadata=%s cli=%s",
                importlib_v,
                cli_v,
            )
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError, IndexError):
        # Not fatal; just couldn't verify drift.
        return


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    assert _session_store is not None

    root = _root_dir()
    if not _is_writable(root):
        raise HTTPException(
            status_code=503,
            detail={
                "code": "unhealthy",
                "message": "Ephemeral workspace is unwritable.",
                "suggestion": None,
            },
        )

    free_disk_mb = _free_disk_mb(root)
    return HealthResponse(
        ok=True,
        embodied_data_version=get_embodied_data_version(),
        uptime_s=int(time.monotonic() - _started_at_monotonic),
        active_session=_session_store.active_session(),
        free_disk_mb=free_disk_mb,
    )


def _is_writable(p: Path) -> bool:
    try:
        return p.is_dir() and os.access(p, os.W_OK)
    except OSError:
        return False


def _free_disk_mb(p: Path) -> int:
    try:
        st = os.statvfs(p)
        free_bytes = st.f_bavail * st.f_frsize
        return int(free_bytes / (1024 * 1024))
    except OSError:
        return 0
