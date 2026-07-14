"""DB-backed, org-scoped conversion job routes (D4-A Refactor #3).

Replaces the F-1 ephemeral `upload.py` / `status.py` / `download.py` trio.
The 5 MVP endpoint shapes are authoritative per `dispatches/D4_specs.md`
§3 amendment #3:

    POST /api/v1/jobs/presign-upload          → R2 presigned PUT + job_id
    POST /api/v1/jobs                          → R2 upload done; kick conversion
    GET  /api/v1/jobs                          → org's jobs, keyset-paginated
    GET  /api/v1/jobs/{job_id}                 → single job (org-scoped)
    GET  /api/v1/jobs/{job_id}/presign-download→ R2 presigned GET + TTL

R3 invariant (NON-OPTIONAL, type-enforced): `org_id` is read ONLY from
`request.state.org_id` (set by `ClerkJWTMiddleware`), NEVER from the
request body. Every `JobStore` call uses the keyword-only
`(job_id, org_id)` API (`app/db/job_store.py:69-121`); every R2 key is
derived from the JWT org via `app/storage/r2.py` (org prefix is the
boundary).

VERIFIED-WORKING CORE PRESERVED: the conversion still runs through the
unchanged `SubprocessRunner.run(sess)` (`app/api/subprocess_runner.py`,
DR-019 evidence: real AgiBot→LeRobot v3 ran E2E in 3.6s). We only swap
the auth + storage layer *around* it: input is pulled from R2 into a
local scratch dir, the verified subprocess runs on that dir exactly as
before, and the result zip is pushed back to R2.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import uuid
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.session_store import Session, _root_dir
from app.api.subprocess_runner import SubprocessRunner, map_outcome_to_state
from app.api.tier_gating import (
    SoftCapExceeded,
    effective_tier,
    enforce_and_resolve,
    max_upload_bytes_for_tier,
)
from app.api.timing_estimates import estimate_total_seconds
from app.db.job_store import JobStore
from app.db.models import JobState, Org
from app.db.session import get_session
from app.schemas import State
from app.storage import r2

# Mirror of `upload.py:39` — supported (from, to) pairs (convert/__init__.py:19-22).
_SUPPORTED_PAIRS = {("agibot", "lerobot-v3"), ("lerobot-v3", "agibot")}

# JobState (DB) ↔ State (API schema) share string values (models.py:82-93 vs
# schemas.py:25-39); the only API-vs-DB skew is `extracting`/`validating`
# which both enums carry, so the .value round-trips 1:1.

router = APIRouter()

# The verified subprocess runner is process-global (single-flight, spec §3).
_runner: SubprocessRunner | None = None
_live_bg_tasks: set[asyncio.Task] = set()  # strong refs; avoid GC (RUF006)


def set_state(runner: SubprocessRunner) -> None:
    global _runner
    _runner = runner


# --------------------------------------------------------------------------
# request / response models
# --------------------------------------------------------------------------


class PresignUploadRequest(BaseModel):
    """NOTE: no org_id field. org_id comes from the JWT (R3). `filename` is
    untrusted; r2.input_key sanitizes it (r2.py:55-64).

    `size_bytes` is the archive's byte length, reported by the browser
    (`File.size`). It is advisory — the client could lie — but it lets us
    reject an oversized upload with a clean 413 BEFORE the R2 presigned URL
    is issued (Track 4 edge-hardening): the bytes never leave the browser,
    so there is no silent R2 failure and nothing to clean up. A client that
    under-reports still hits the converter's own memory limit downstream,
    which already maps to a typed `oom_suspected` failure
    (`subprocess_runner.py:323-331`). `ge=1` rejects a non-positive size; the
    optional default keeps older clients working (they skip the size gate).
    """

    filename: str = Field(..., min_length=1, max_length=512)
    from_format: str
    to_format: str
    size_bytes: int | None = Field(default=None, ge=1)


class PresignUploadResponse(BaseModel):
    job_id: str
    upload_url: str
    key: str
    expires_in: int


class CreateJobRequest(BaseModel):
    """Client calls this AFTER the R2 PUT completes. Only the job_id is
    needed — org_id from JWT, input key already stored on the row."""

    job_id: str
    max_episodes: int | None = None


class JobView(BaseModel):
    job_id: str
    state: State
    from_format: str | None = None
    to_format: str | None = None
    error_code: str | None = None
    error_msg: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None


class JobListResponse(BaseModel):
    jobs: list[JobView]
    next_before: datetime | None = None


class PresignDownloadResponse(BaseModel):
    download_url: str
    expires_in: int


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


class BadArchiveError(Exception):
    """The uploaded file isn't a readable archive (wrong type, corrupt, or a
    path-traversal entry). Raised by `_extract_archive`; `_run_conversion`
    maps it to a typed `bad_archive` failure rather than `internal_error`,
    so the customer sees a readable reason in the dashboard."""


def _err(status: int, code: str, message: str, suggestion: str | None = None) -> HTTPException:
    return HTTPException(
        status_code=status,
        detail={"code": code, "message": message, "suggestion": suggestion},
    )


def _require_org(request: Request) -> str:
    """R3 enforcement point: org_id ONLY from JWT-populated request.state.

    The middleware guarantees this is set on every non-exempt route, but we
    fail loudly (500) rather than silently scope to a wrong/empty org if the
    wiring ever regresses.
    """
    org_id = getattr(request.state, "org_id", None)
    if not isinstance(org_id, str) or not org_id:
        raise _err(500, "auth_state_missing", "Authenticated org not resolved.")
    return org_id


def _resolve_max_episodes(tier_cap: int | None, client_ask: int | None) -> int | None:
    """Effective episode limit = the tightest of (tier cap, client request).

    - Free tier (`tier_cap=1`): always 1, even if the client asks for more
      or nothing — the cap is a hard ceiling, not a default.
    - Paid (`tier_cap=None`): honor the client's optional `max_episodes`
      (e.g. a quick smoke run), else None == full convert.
    `client_ask` <= 0 is ignored (a non-positive cap would convert nothing).
    """
    asks = client_ask if (isinstance(client_ask, int) and client_ask > 0) else None
    candidates = [c for c in (tier_cap, asks) if c is not None]
    return min(candidates) if candidates else None


def _job_to_view(job) -> JobView:
    return JobView(
        job_id=str(job.id),
        state=State(job.state.value),
        from_format=job.from_format,
        to_format=job.to_format,
        error_code=job.error_code,
        error_msg=job.error_msg,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
    )


# --------------------------------------------------------------------------
# endpoints
# --------------------------------------------------------------------------


@router.post("/jobs/presign-upload", response_model=PresignUploadResponse, status_code=201)
async def presign_upload(
    request: Request,
    body: PresignUploadRequest,
    db: AsyncSession = Depends(get_session),
) -> PresignUploadResponse:
    """Create a `pending` job row and return an R2 presigned PUT URL.

    The job_id is server-generated (`Job.id` UUID, models.py:209-213); the
    R2 key embeds the JWT org prefix so the upload lands in the caller's
    namespace and nowhere else.
    """
    org_id = _require_org(request)

    pair = (body.from_format, body.to_format)
    if body.from_format == body.to_format or pair not in _SUPPORTED_PAIRS:
        raise _err(
            400,
            "invalid_format_pair",
            "This conversion direction isn't supported.",
            "Try AgiBot -> LeRobot v3, or the reverse.",
        )

    # Track 4 edge-hardening: tier-aware upload-size gate. Reject an
    # oversized archive HERE — before the R2 presigned URL exists — so the
    # bytes never leave the browser. Without this gate a 5 GB file would PUT
    # straight to R2 and then either fail opaquely or OOM the converter into
    # a generic `internal_error`. The cap is per-tier (billing_config.py
    # MAX_UPLOAD_BYTES); the tier is server-trusted (`effective_tier`, same
    # cancel-grace-aware read POST /jobs uses). 413 Payload Too Large with
    # the house `{code,message,suggestion}` envelope.
    if body.size_bytes is not None:
        org_for_cap = (await db.execute(select(Org).where(Org.id == org_id))).scalar_one_or_none()
        if org_for_cap is None:
            raise _err(500, "org_not_provisioned", "Organization state not resolved.")
        tier = await effective_tier(db, org_for_cap)
        cap = max_upload_bytes_for_tier(tier)
        if cap is not None and body.size_bytes > cap:
            cap_mb = cap // (1024 * 1024)
            raise _err(
                413,
                "upload_too_large",
                f"This file is larger than your {tier.value} plan allows ({cap_mb} MB max).",
                "Upgrade your plan for a higher limit, or run the CLI "
                "locally for unlimited size: `pip install embodied-data`.",
            )

    store = JobStore(db)
    job = await store.create(
        org_id=org_id,
        user_id=getattr(request.state, "user_id", None),
        from_format=body.from_format,
        to_format=body.to_format,
    )
    presigned = r2.presign_upload(org_id=org_id, job_id=job.id, filename=body.filename)
    # Persist the input key so POST /jobs needn't trust a body-supplied key.
    await store.update_state(
        job_id=job.id,
        org_id=org_id,
        state=JobState.pending,
        input_uri=str(presigned["key"]),
    )
    return PresignUploadResponse(
        job_id=str(job.id),
        upload_url=str(presigned["url"]),
        key=str(presigned["key"]),
        expires_in=int(presigned["expires_in"]),
    )


@router.post("/jobs", status_code=202, response_model=None)
async def create_job(
    request: Request,
    body: CreateJobRequest,
    db: AsyncSession = Depends(get_session),
) -> JobView | JSONResponse:
    """Notify the backend that the R2 upload completed; kick off conversion.

    `(job_id, org_id)` tuple lookup enforces R3: a job_id forged from
    another org returns 404, indistinguishable from never-existed
    (`job_store.py:69-78`).
    """
    assert _runner is not None
    org_id = _require_org(request)

    try:
        job_uuid = uuid.UUID(body.job_id)
    except ValueError as exc:
        raise _err(400, "invalid_job_id", "job_id is not a valid identifier.") from exc

    store = JobStore(db)
    job = await store.get(job_id=job_uuid, org_id=org_id)
    if job is None:
        raise _err(404, "job_not_found", "We don't know that job.")
    if job.state != JobState.pending:
        raise _err(409, "job_already_started", "This job has already been submitted.")
    if not job.input_uri:
        raise _err(409, "no_input", "No uploaded input is associated with this job.")

    # Story #8 tier gating. org_id came from the JWT only (R3); we look up
    # the server-trusted tier and run BOTH gates (monthly soft cap +
    # episode cap). This WRAPS the DR-019 conversion call — it decides
    # whether/how to invoke, it does not alter the conversion itself.
    org = (await db.execute(select(Org).where(Org.id == org_id))).scalar_one_or_none()
    if org is None:
        raise _err(500, "org_not_provisioned", "Organization state not resolved.")
    try:
        tier_max_episodes = await enforce_and_resolve(db, org)
    except SoftCapExceeded as exc:
        # 402 Payment Required. Upgrade-prompt email is deferred (no Resend
        # credential in the D4-B env set); we emit a structured log line so
        # the (later) email job / dashboard can pick it up. Brief: "402 +
        # (later) upgrade email".
        import logging

        logging.getLogger("agibridge.billing").info(
            "soft_cap_exceeded org=%s tier=%s used=%d cap=%d "
            "(upgrade-prompt email deferred — no Resend creds in D4-B env)",
            org_id,
            exc.tier.value,
            exc.used,
            exc.cap,
        )
        raise _err(
            402,
            "soft_cap_exceeded",
            f"You've reached your {exc.tier.value} plan's monthly conversion limit ({exc.cap}).",
            "Upgrade your plan to keep converting this month.",
        ) from exc

    # The client MAY request fewer episodes, but can NEVER exceed the tier
    # cap. Effective = min of (tier cap, client ask), ignoring None.
    effective_max_episodes = _resolve_max_episodes(tier_max_episodes, body.max_episodes)

    # Single-flight: at most one live conversion (spec §3). 429 if busy —
    # frontend backs off (Cycle H §3 cadence). Tracked via the module
    # background-task set; SubprocessRunner enforces its own at-most-one
    # internally too.
    if _live_bg_tasks:
        return _busy_429()

    await store.update_state(
        job_id=job_uuid,
        org_id=org_id,
        state=JobState.extracting,
        started_at=datetime.now(UTC),
    )

    task = asyncio.create_task(
        _run_conversion(
            job_id=job_uuid,
            org_id=org_id,
            input_key=job.input_uri,
            from_format=job.from_format or "agibot",
            to_format=job.to_format or "lerobot-v3",
            max_episodes=effective_max_episodes,
        )
    )
    _live_bg_tasks.add(task)
    task.add_done_callback(_live_bg_tasks.discard)

    refreshed = await store.get(job_id=job_uuid, org_id=org_id)
    assert refreshed is not None
    return _job_to_view(refreshed)


@router.get("/jobs", response_model=JobListResponse)
async def list_jobs(
    request: Request,
    limit: int = 50,
    before: datetime | None = None,
    db: AsyncSession = Depends(get_session),
) -> JobListResponse:
    """Org-scoped, most-recent-first, keyset pagination on created_at.

    `list_for_org` is the ONLY listing method — there is no cross-org
    variant (`job_store.py:80-95`). R3 scenario 2 + 6 pin this.
    """
    org_id = _require_org(request)
    limit = max(1, min(limit, 100))
    store = JobStore(db)
    jobs = await store.list_for_org(org_id=org_id, limit=limit, before=before)
    views = [_job_to_view(j) for j in jobs]
    next_before = views[-1].created_at if len(views) == limit else None
    return JobListResponse(jobs=views, next_before=next_before)


@router.get("/jobs/{job_id}", response_model=JobView)
async def get_job(
    request: Request,
    job_id: str,
    db: AsyncSession = Depends(get_session),
) -> JobView:
    """Single job status. Backend enforces the `(job_id, org_id)` tuple
    lookup (amendment #3); cross-org → 404 (side-channel-safe)."""
    org_id = _require_org(request)
    try:
        job_uuid = uuid.UUID(job_id)
    except ValueError as exc:
        raise _err(404, "job_not_found", "We don't know that job.") from exc
    store = JobStore(db)
    job = await store.get(job_id=job_uuid, org_id=org_id)
    if job is None:
        raise _err(404, "job_not_found", "We don't know that job.")
    return _job_to_view(job)


@router.get("/jobs/{job_id}/presign-download", response_model=PresignDownloadResponse)
async def presign_download(
    request: Request,
    job_id: str,
    db: AsyncSession = Depends(get_session),
) -> PresignDownloadResponse:
    """Return an R2 presigned GET for a finished job's output.

    Defense-in-depth: `r2.presign_download` re-derives the expected
    `orgs/{org_id}/jobs/{job_id}/` prefix and raises PermissionError on
    mismatch (`r2.py:92-117`); we map that to 404 so a corrupted row never
    leaks another org's object.
    """
    org_id = _require_org(request)
    try:
        job_uuid = uuid.UUID(job_id)
    except ValueError as exc:
        raise _err(404, "job_not_found", "We don't know that job.") from exc

    store = JobStore(db)
    job = await store.get(job_id=job_uuid, org_id=org_id)
    if job is None:
        raise _err(404, "job_not_found", "We don't know that job.")
    if job.state != JobState.done:
        raise _err(409, "not_ready", "The conversion isn't finished yet.")
    if not job.output_uri:
        raise _err(410, "download_expired", "This job's output is no longer available.")

    try:
        url = r2.presign_download(org_id=org_id, job_id=job_uuid, output_key_value=job.output_uri)
    except PermissionError as exc:
        # R3 defense-in-depth tripped: treat as not-found, never reveal.
        raise _err(404, "job_not_found", "We don't know that job.") from exc
    return PresignDownloadResponse(download_url=url, expires_in=r2.DOWNLOAD_TTL_S)


# --------------------------------------------------------------------------
# background conversion — wraps the VERIFIED-WORKING subprocess unchanged
# --------------------------------------------------------------------------


def _busy_429() -> JSONResponse:
    return JSONResponse(
        status_code=429,
        content={
            "code": "busy",
            "message": "Another conversion is in progress.",
            "suggestion": "Please retry in about 90 seconds.",
        },
        headers={"Retry-After": "90"},
    )


async def _run_conversion(
    *,
    job_id: uuid.UUID,
    org_id: str,
    input_key: str,
    from_format: str,
    to_format: str,
    max_episodes: int | None,
) -> None:
    """Pull input from R2 → run the UNCHANGED verified subprocess → push the
    result zip to R2 → update the org-scoped JobStore row.

    Each terminal state write goes through `JobStore.update_state(*,
    job_id, org_id, ...)` so R3 holds even on the background path.
    """
    assert _runner is not None

    # Local scratch workspace matching the Session contract the verified
    # subprocess runner expects (subprocess_runner.py:_build_cmd reads
    # sess.in_dir/dataset, sess.out_dir, sess.from_format, sess.to_format).
    root = _root_dir() / f"job-{job_id}"
    sess = Session(session_id=str(job_id), root=root)
    sess.from_format = from_format
    sess.to_format = to_format
    # Story #8: thread the gate-resolved cap onto the Session so
    # `subprocess_runner._build_cmd` appends `--max-episodes N`. None ==
    # uncapped == DR-019 byte-identical argv.
    sess.max_episodes = max_episodes
    extract_dir = sess.in_dir / "dataset"

    # Fresh DB session for the background task (the request-scoped one is
    # closed once create_job returns).
    from app.db.session import get_sessionmaker

    sm = get_sessionmaker()

    try:
        sess.in_dir.mkdir(parents=True, exist_ok=True)
        sess.out_dir.mkdir(parents=True, exist_ok=True)
        extract_dir.mkdir(parents=True, exist_ok=True)

        archive_path = sess.in_dir / "upload.archive"
        await asyncio.to_thread(_r2_download_to, input_key, archive_path)
        await asyncio.to_thread(_extract_archive, archive_path, extract_dir)

        sess.estimated_total_s = estimate_total_seconds(
            from_format=from_format, to_format=to_format, in_dir=extract_dir
        )

        async with sm() as db:
            await JobStore(db).update_state(
                job_id=job_id,
                org_id=org_id,
                state=JobState.running,
                estimated_total_s=sess.estimated_total_s,
            )
            await db.commit()

        # ---- VERIFIED-WORKING CORE: unchanged subprocess invocation ----
        # NOTE: SubprocessRunner.run() manages its own internal _lock for
        # live-proc bookkeeping (subprocess_runner.py:140). asyncio.Lock is
        # NOT reentrant — wrapping this call in `async with _runner._lock`
        # would deadlock. Single-flight is enforced upstream by the
        # `_live_bg_tasks` guard in create_job.
        outcome = await _runner.run(sess)
        # ----------------------------------------------------------------

        state, code, message, _suggestion = map_outcome_to_state(outcome)

        if state == State.done:
            result_zip = sess.result_zip
            await asyncio.to_thread(_zip_output, sess.out_dir, result_zip)
            output_key = r2.output_key(org_id, job_id)
            await asyncio.to_thread(_r2_upload_from, result_zip, output_key)
            async with sm() as db:
                await JobStore(db).update_state(
                    job_id=job_id,
                    org_id=org_id,
                    state=JobState.done,
                    output_uri=output_key,
                    finished_at=datetime.now(UTC),
                )
                await db.commit()
        else:
            async with sm() as db:
                await JobStore(db).update_state(
                    job_id=job_id,
                    org_id=org_id,
                    state=JobState.failed,
                    error_code=code,
                    error_msg=message,
                    stderr_tail=outcome.stderr_tail or None,
                    finished_at=datetime.now(UTC),
                )
                await db.commit()
    except BadArchiveError as exc:
        # Track 4 edge-hardening: a non-archive, a corrupt archive, or a
        # path-traversal entry — caught from `_extract_archive` BEFORE the
        # converter ever sees the input. Without this branch it fell through
        # to the generic handler below as `error_code=internal_error`,
        # `error_msg="ValueError"` — a blank-looking failed row with no
        # readable reason. Surface a typed code + a customer-readable
        # message so JobsTable's "View error" shows what actually went
        # wrong. The reason text is structural (archive shape only), never
        # user file content (spec §10 item 11).
        try:
            async with sm() as db:
                await JobStore(db).update_state(
                    job_id=job_id,
                    org_id=org_id,
                    state=JobState.failed,
                    error_code="bad_archive",
                    error_msg=(
                        "We couldn't read this as an AgiBot/LeRobot dataset: "
                        f"{exc}. Upload a .zip or .tar.gz of the dataset folder."
                    ),
                    finished_at=datetime.now(UTC),
                )
                await db.commit()
        except Exception:
            pass
    except Exception as exc:
        # Best-effort failure record; never log user-attributable input.
        try:
            async with sm() as db:
                await JobStore(db).update_state(
                    job_id=job_id,
                    org_id=org_id,
                    state=JobState.failed,
                    error_code="internal_error",
                    error_msg=f"{type(exc).__name__}",
                    finished_at=datetime.now(UTC),
                )
                await db.commit()
        except Exception:
            pass
    finally:
        await asyncio.to_thread(shutil.rmtree, root, ignore_errors=True)


def _r2_download_to(key: str, dest: Path) -> None:
    """Stream an R2 object to a local file (sync; called via to_thread)."""
    client = r2._client()
    client.download_file(r2._bucket(), key, str(dest))


def _r2_upload_from(src: Path, key: str) -> None:
    client = r2._client()
    client.upload_file(str(src), r2._bucket(), key)


def _extract_archive(archive: Path, dest: Path) -> None:
    """Detect zip/tar.gz by magic bytes and extract with the SAME safety
    posture as the F-1 upload path (upload.py:202-266) — path-traversal
    containment + tar `filter='data'`. Reused so the verified subprocess
    sees an identically-structured input tree.

    Track 4 edge-hardening: every customer-reachable failure here raises
    `BadArchiveError` with a structural, readable reason (never user file
    content). `_run_conversion` maps it to a typed `bad_archive` job
    failure — previously a corrupt/wrong file fell through as the opaque
    `internal_error` / `"ValueError"` failed row."""
    import tarfile

    with archive.open("rb") as f:
        head = f.read(4)
    if head[:4] == b"\x50\x4b\x03\x04":
        try:
            zf_cm = zipfile.ZipFile(archive)
        except zipfile.BadZipFile as exc:
            raise BadArchiveError("the .zip archive is corrupt or incomplete") from exc
        with zf_cm as zf:
            dest_resolved = dest.resolve()
            for zi in zf.infolist():
                target = (dest / zi.filename).resolve()
                if (
                    not str(target).startswith(str(dest_resolved) + os.sep)
                    and target != dest_resolved
                ):
                    raise BadArchiveError("an archive entry escapes the extraction folder")
                if zi.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(zi) as s, target.open("wb") as d:
                    shutil.copyfileobj(s, d)
    elif head[:2] == b"\x1f\x8b":
        try:
            with tarfile.open(archive, mode="r:gz") as tf:
                tf.extractall(dest, filter="data")
        except (tarfile.ReadError, tarfile.TarError, EOFError) as exc:
            raise BadArchiveError("the .tar.gz archive is corrupt or incomplete") from exc
    else:
        raise BadArchiveError("the file isn't a .zip or .tar.gz archive (or it is corrupt)")


def _zip_output(out_dir: Path, dest: Path) -> None:
    """Pack the verified subprocess output dir into result.zip (STORED — the
    LeRobot v3 parquet/video is already compressed). Identical to
    upload.py:302-312."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_STORED) as zf:
        for root, _, files in os.walk(out_dir):
            for fname in files:
                full = Path(root) / fname
                zf.write(full, full.relative_to(out_dir))
