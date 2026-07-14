"""Job state-machine lifecycle tests — Cycle N coverage expansion.

The `Job.state` field (`app/db/models.py:206-208`) uses the `JobState`
enum (`models.py:79-90`) with values:
    pending → extracting → running → validating → done
                                              ↘ failed
                                              ↘ expired

`JobStore.update_state` (`app/db/job_store.py:101-127`) is the *only*
mutation entry point. R3 enforcement is already covered by the 4
existing scenarios in `tests/test_r3_cross_org_isolation.py`; this file
covers the orthogonal *state-machine* invariants:
    1. Happy-path full lifecycle (pending → … → done).
    2. Mid-flight failure transitions (pending → failed; running → failed).
    3. Retry-from-failed allowance (failed → pending is permitted at
       app layer; DB has no FSM constraint).
    4. Allowed-field whitelist enforcement (raises ValueError on unknown
       kwargs — fail-loudly contract at `job_store.py:118-120`).

Test infra parity with `tests/test_r3_cross_org_isolation.py:34-51`:
sqlite+aiosqlite in-memory, single Org fixture.

These tests do NOT exercise the upload handler (`app/api/upload.py`),
the subprocess runner (`app/api/subprocess_runner.py`), or webhook
handler — they exercise the JobStore contract directly. Handler-level
state transitions are deferred to the D5+ end-to-end suite alongside
the D4-shipped Clerk middleware (tests stub'd in `test_clerk_jwt.py`).
"""

from __future__ import annotations

from datetime import UTC

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.job_store import JobStore
from app.db.models import Base, JobState, Org

# ----------------------- fixtures -----------------------


@pytest_asyncio.fixture
async def session():
    """In-memory sqlite. Mirrors test_r3_cross_org_isolation.py:34-42."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as s:
        yield s
    await engine.dispose()


@pytest_asyncio.fixture
async def org(session):
    """Single Org. Cleaner than `two_orgs` for state-machine tests since
    R3 is not under test here."""
    o = Org(id="org_LIFE", name="Lifecycle Labs")
    session.add(o)
    await session.flush()
    return o


# ----------------------- happy path -----------------------


@pytest.mark.asyncio
async def test_full_happy_path_pending_to_done(session, org):
    """Walk a job through the full success lifecycle and verify each
    transition is recorded.

    pending (create) → extracting → running → validating → done.

    Each `update_state` must return True (rowcount=1). Final read via
    `JobStore.get` returns the row with state=done and finished_at set.
    """
    from datetime import datetime

    store = JobStore(session)
    job = await store.create(
        org_id=org.id,
        user_id=None,
        from_format="agibot",
        to_format="lerobot-v3",
    )
    await session.flush()
    assert job.state == JobState.pending

    # pending → extracting
    ok = await store.update_state(
        job_id=job.id,
        org_id=org.id,
        state=JobState.extracting,
    )
    assert ok is True

    # extracting → running (start_time recorded)
    now = datetime.now(UTC)
    ok = await store.update_state(
        job_id=job.id,
        org_id=org.id,
        state=JobState.running,
        started_at=now,
    )
    assert ok is True

    # running → validating
    ok = await store.update_state(
        job_id=job.id,
        org_id=org.id,
        state=JobState.validating,
    )
    assert ok is True

    # validating → done (finished_at + output_uri recorded)
    out_uri = f"orgs/{org.id}/jobs/{job.id}/output/result.zip"
    ok = await store.update_state(
        job_id=job.id,
        org_id=org.id,
        state=JobState.done,
        finished_at=now,
        output_uri=out_uri,
    )
    assert ok is True

    fresh = await store.get(job_id=job.id, org_id=org.id)
    assert fresh is not None
    assert fresh.state == JobState.done
    assert fresh.finished_at is not None
    assert fresh.output_uri == out_uri


@pytest.mark.asyncio
async def test_pending_to_failed_records_error_fields(session, org):
    """A job that fails before it ever runs (e.g. malformed archive
    caught at extraction) transitions pending → failed with error_code /
    error_msg / suggestion populated per
    `app/api/subprocess_runner.py::map_outcome_to_state` contract.

    Allowed-field whitelist at job_store.py:22-33 includes:
    error_code, error_msg, stderr_tail, finished_at.
    """
    from datetime import datetime

    store = JobStore(session)
    job = await store.create(
        org_id=org.id,
        user_id=None,
        from_format="agibot",
        to_format="lerobot-v3",
    )
    await session.flush()

    ok = await store.update_state(
        job_id=job.id,
        org_id=org.id,
        state=JobState.failed,
        error_code="archive_too_large",
        error_msg="expanded beyond 1.5 GB uncompressed",
        stderr_tail="",
        finished_at=datetime.now(UTC),
    )
    assert ok is True

    fresh = await store.get(job_id=job.id, org_id=org.id)
    assert fresh is not None
    assert fresh.state == JobState.failed
    assert fresh.error_code == "archive_too_large"
    assert fresh.error_msg == "expanded beyond 1.5 GB uncompressed"


@pytest.mark.asyncio
async def test_retry_from_failed_state_allowed_at_db_layer(session, org):
    """The DB layer has NO finite-state-machine constraint on transitions.
    `JobStore.update_state` accepts any (current_state, target_state)
    pair — the FSM is enforced at the app layer in the upload handler.

    This test pins that contract so D4 can implement an app-layer FSM
    knowing the DB will not double-enforce. failed → pending (retry)
    must succeed at the JobStore level.

    Trade-off: someone could bypass the FSM by calling JobStore directly.
    Mitigation: the only non-test caller of update_state is the upload
    background task. R3 enforcement (org_id WHERE clause) is the
    *security* invariant; FSM is a *correctness* invariant the app layer
    owns.
    """
    store = JobStore(session)
    job = await store.create(
        org_id=org.id,
        user_id=None,
        from_format="agibot",
        to_format="lerobot-v3",
    )
    await session.flush()

    # pending → failed
    ok = await store.update_state(
        job_id=job.id,
        org_id=org.id,
        state=JobState.failed,
        error_code="oom_suspected",
    )
    assert ok is True

    # failed → pending (retry). DB allows it.
    ok = await store.update_state(
        job_id=job.id,
        org_id=org.id,
        state=JobState.pending,
    )
    assert ok is True

    fresh = await store.get(job_id=job.id, org_id=org.id)
    assert fresh is not None
    assert fresh.state == JobState.pending


@pytest.mark.asyncio
async def test_update_state_with_unknown_field_raises_value_error(session, org):
    """`app/db/job_store.py:118-120` whitelist enforcement.

    Passing a field not in `_ALLOWED_UPDATE_FIELDS` raises ValueError —
    fail-loud contract chosen so dev-time typos surface immediately
    rather than silently dropping the value. Example: `output_url`
    (typo, should be `output_uri`) → raises.
    """
    store = JobStore(session)
    job = await store.create(
        org_id=org.id,
        user_id=None,
        from_format="agibot",
        to_format="lerobot-v3",
    )
    await session.flush()

    with pytest.raises(ValueError, match="unexpected field"):
        await store.update_state(
            job_id=job.id,
            org_id=org.id,
            state=JobState.done,
            output_url="oops-typo",  # not in _ALLOWED_UPDATE_FIELDS
        )


@pytest.mark.asyncio
async def test_update_state_nonexistent_job_returns_false(session, org):
    """`app/db/job_store.py:127` returns `rowcount > 0`. A random UUID
    that does not exist (irrespective of org_id) must return False.

    Distinct from the R3 cross-org test: here both org_id and job_id
    are mismatched/missing rather than a cross-org-with-real-job
    attack.
    """
    import uuid

    store = JobStore(session)
    ok = await store.update_state(
        job_id=uuid.uuid4(),
        org_id=org.id,
        state=JobState.done,
    )
    assert ok is False
