# DEPRECATED — superseded by app/api/jobs.py (D4-A Refactor #3).
# Not mounted in app/main.py. F-1 single-tenant ephemeral upload path;
# replaced by the org-scoped presign-upload → POST /jobs R2 flow. Retained
# only for git-history reference; safe to delete once jobs.py is proven in
# prod (orchestrator pre-merge-check).
"""POST /api/v1/upload. Accepts the archive, kicks off conversion.

Spec §2.1, §4 step 1, §6 (size caps), §7 (security mitigations).

Lib citations:
- `convert/__init__.py:19-22` — `_SUPPORTED` tuple of accepted (from, to) pairs.
- `_emit.py:32-46` — error contract for the underlying CLI.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import tarfile
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from ulid import ULID

from app.api.session_store import Session, SessionStore
from app.api.subprocess_runner import SubprocessRunner, update_session_from_outcome
from app.api.timing_estimates import estimate_total_seconds
from app.schemas import State, UploadAccepted

# Per spec §2.1: 800 MB compressed at HTTP layer.
MAX_COMPRESSED_BYTES = 800 * 1024 * 1024
# Per spec §2.1, §6: 1.5 GB uncompressed.
MAX_UNCOMPRESSED_BYTES = 1500 * 1024 * 1024
# Magic bytes (spec §7 MIME spoofing row).
ZIP_MAGIC = b"\x50\x4b\x03\x04"
GZIP_MAGIC = b"\x1f\x8b"

# Per `convert/__init__.py:19-22`. Mirrored here for the early-fail check
# (spec §2.1 — fail fast before subprocess invocation).
_SUPPORTED_PAIRS = {("agibot", "lerobot-v3"), ("lerobot-v3", "agibot")}

router = APIRouter()

_session_store: SessionStore | None = None
_runner: SubprocessRunner | None = None
_live_bg_task: asyncio.Task | None = None  # avoid GC of background task per RUF006


def set_state(store: SessionStore, runner: SubprocessRunner) -> None:
    global _session_store, _runner
    _session_store = store
    _runner = runner


def _err(status: int, code: str, message: str, suggestion: str | None = None) -> HTTPException:
    """Standard ApiError envelope (matches schemas.ApiError)."""
    return HTTPException(
        status_code=status,
        detail={"code": code, "message": message, "suggestion": suggestion},
    )


@router.post("/upload", status_code=202, response_model=UploadAccepted)
async def upload(
    file: UploadFile = File(...),
    from_format: str = Form(...),
    to_format: str = Form(...),
    max_episodes: int | None = Form(None),
) -> JSONResponse:
    """Accept the archive, kick off conversion. Returns 202 + status_url."""
    assert _session_store is not None
    assert _runner is not None

    # 1. Validate format pair (spec §2.1, §5 invalid_format_pair).
    pair = (from_format, to_format)
    if from_format == to_format or pair not in _SUPPORTED_PAIRS:
        raise _err(
            400,
            "invalid_format_pair",
            "This conversion direction isn't supported yet.",
            "Try AgiBot -> LeRobot v3, or the reverse.",
        )

    # 2. Single-flight: 429 if another conversion holds the lock (spec §3).
    if _session_store.global_lock.locked():
        return JSONResponse(
            status_code=429,
            content={
                "code": "busy",
                "message": "Another run is in progress.",
                "suggestion": "Please try again in about 90 seconds.",
            },
            headers={"Retry-After": "90"},
        )

    # 3. Issue session id, mkdir.
    session_id = str(ULID())
    sess = await _session_store.create(session_id)
    sess.from_format = from_format
    sess.to_format = to_format

    # 4. Stream upload to disk with size cap. fail-fast 413 before extraction.
    try:
        await _stream_upload(file, sess.in_dir / "upload.archive")
    except _SizeExceeded as exc:
        await _cleanup(sess)
        raise _err(
            413,
            "archive_too_large",
            "The archive is larger than the 800 MB limit on this hosted environment.",
            "For larger datasets, run the CLI locally: `pip install embodied-data`.",
        ) from exc
    except OSError as exc:  # ENOSPC etc.
        await _cleanup(sess)
        raise _err(500, "internal_error", "Failed to write upload to disk.", str(exc)) from exc

    # 5. Magic-byte / archive-type check (spec §7 MIME spoofing).
    archive_path = sess.in_dir / "upload.archive"
    archive_kind = _detect_archive_kind(archive_path)
    if archive_kind is None:
        await _cleanup(sess)
        raise _err(
            415,
            "unsupported_archive_type",
            "Only .zip and .tar.gz archives are accepted here.",
            None,
        )

    # 6. Synchronous extract with bomb guard (spec §7 zip/tar bomb).
    extract_dir = sess.in_dir / "dataset"
    extract_dir.mkdir(exist_ok=True)
    try:
        await asyncio.to_thread(_safe_extract, archive_path, archive_kind, extract_dir)
    except _SizeExceeded as exc:
        await _cleanup(sess)
        raise _err(
            413,
            "archive_too_large",
            "The archive expanded beyond the 1.5 GB uncompressed limit.",
            "Try `--max-episodes` slicing or run the CLI locally.",
        ) from exc
    except _UnsafeArchive as exc:
        await _cleanup(sess)
        raise _err(
            415, "mime_spoofed", str(exc), "Re-export as a plain .zip and try again."
        ) from exc

    # 7. Hand off to background task. Return 202 immediately.
    sess.estimated_total_s = estimate_total_seconds(
        from_format=from_format, to_format=to_format, in_dir=extract_dir
    )
    sess.write_status()
    # Reference held in module-global to avoid GC. Per spec §3 single-flight,
    # there is at most one live task; we don't need a per-session set.
    global _live_bg_task
    _live_bg_task = asyncio.create_task(_run_background(sess, max_episodes))

    return JSONResponse(
        status_code=202,
        content={
            "session_id": sess.session_id,
            "status_url": f"/api/v1/status/{sess.session_id}",
            "expires_at": sess.expires_at.isoformat(),
            "note": (
                "This run is ephemeral. Files are kept for 30 minutes, "
                "then deleted. No accounts, no storage."
            ),
        },
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _SizeExceeded(Exception):
    pass


class _UnsafeArchive(Exception):
    pass


async def _stream_upload(file: UploadFile, dest: Path) -> None:
    """Stream multipart body to disk in 64 KB chunks; abort if cap exceeded.

    Per spec §7 'Large memory in extraction': stream, never buffer.
    """
    written = 0
    chunk_size = 64 * 1024
    with dest.open("wb") as f:
        while True:
            chunk = await file.read(chunk_size)
            if not chunk:
                break
            written += len(chunk)
            if written > MAX_COMPRESSED_BYTES:
                raise _SizeExceeded()
            f.write(chunk)


def _detect_archive_kind(path: Path) -> str | None:
    """Return 'zip' | 'tar.gz' | None based on magic bytes (spec §7)."""
    with path.open("rb") as f:
        head = f.read(4)
    if head[:4] == ZIP_MAGIC:
        return "zip"
    if head[:2] == GZIP_MAGIC:
        return "tar.gz"
    return None


def _safe_extract(archive: Path, kind: str, dest: Path) -> None:
    """Extract with path-traversal containment + bomb guard.

    Per spec §7:
    - zipfile: explicit Path(target).resolve() containment check per entry.
    - tarfile: extractall(filter='data') for symlink/abs-path safety (HP-8).
    - Pre-check sum of entry uncompressed sizes; runtime byte counter as belt-and-braces.
    """
    dest_resolved = dest.resolve()
    written = 0
    if kind == "zip":
        with zipfile.ZipFile(archive) as zf:
            # Pre-check: sum of declared file_size.
            total_declared = sum(zi.file_size for zi in zf.infolist())
            if total_declared > MAX_UNCOMPRESSED_BYTES:
                raise _SizeExceeded()
            for zi in zf.infolist():
                # Path traversal containment (spec §7 'Path traversal in upload zip').
                target = (dest / zi.filename).resolve()
                if (
                    not str(target).startswith(str(dest_resolved) + os.sep)
                    and target != dest_resolved
                ):
                    raise _UnsafeArchive(
                        "archive contains entry that escapes the extraction directory"
                    )
                if zi.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(zi) as src, target.open("wb") as dst:
                    while chunk := src.read(64 * 1024):
                        written += len(chunk)
                        if written > MAX_UNCOMPRESSED_BYTES:
                            raise _SizeExceeded()
                        dst.write(chunk)
    elif kind == "tar.gz":
        with tarfile.open(archive, mode="r:gz") as tf:
            # Pre-check via getmembers; tarfile may have no size on dirs etc.
            total_declared = sum(m.size for m in tf.getmembers() if m.isfile())
            if total_declared > MAX_UNCOMPRESSED_BYTES:
                raise _SizeExceeded()
            # tarfile filter='data' handles symlink/abs-path safety per PEP 706
            # https://docs.python.org/3.12/library/tarfile.html#tarfile-extraction-filter
            tf.extractall(dest, filter="data")
            # Byte-count pass after extraction (belt-and-braces; we already
            # pre-checked via getmembers but tar can lie about sizes).
            for root, _, files in os.walk(dest):
                for fname in files:
                    written += os.path.getsize(os.path.join(root, fname))
                    if written > MAX_UNCOMPRESSED_BYTES:
                        raise _SizeExceeded()
    else:
        raise _UnsafeArchive(f"unknown archive kind: {kind}")


async def _cleanup(sess: Session) -> None:
    """Best-effort directory cleanup on early-failure paths.
    Logs nothing user-attributable per spec §10 item 11."""
    try:
        await asyncio.to_thread(shutil.rmtree, sess.root, ignore_errors=True)
    finally:
        assert _session_store is not None
        await _session_store.remove(sess.session_id)


async def _run_background(sess: Session, max_episodes: int | None) -> None:
    """Hold GlobalLock, run subprocess, write zip, update status."""
    assert _session_store is not None
    assert _runner is not None

    async with _session_store.global_lock:
        sess.state = State.extracting
        sess.started_at = datetime.now(UTC)
        sess.write_status()

        sess.state = State.running
        sess.write_status()

        outcome = await _runner.run(sess)
        update_session_from_outcome(sess, outcome)

        if sess.state == State.done:
            # Build result.zip from out/. Streamed by /download.
            await asyncio.to_thread(_zip_output, sess)

        sess.write_status()


def _zip_output(sess: Session) -> None:
    """Produce result.zip from sess.out_dir (stored, no compression for speed
    on already-compressed video; small text files don't matter at 1.5 GB)."""
    out_zip = sess.result_zip
    out_zip.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_zip, "w", compression=zipfile.ZIP_STORED) as zf:
        for root, _, files in os.walk(sess.out_dir):
            for fname in files:
                full = Path(root) / fname
                arc = full.relative_to(sess.out_dir)
                zf.write(full, arc)
