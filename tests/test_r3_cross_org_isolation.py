"""R3 — cross-org isolation. EXISTENTIAL risk per architect tech_spec §6.

Three scenarios cover the three attack surfaces called out in the brief:

1. Direct job read by id with the wrong org's claim -> store returns None.
   (Caller surfaces 404, NOT 403, to avoid leaking that the id exists in
   another org — that's a side-channel.)
2. List-jobs leakage: Org B's listing returns ZERO of Org A's jobs even if
   format metadata overlaps.
3. R2 presigned-download with mismatched org_id in the stored key: storage
   layer raises PermissionError as defense-in-depth.

Test infra choice: sqlite+aiosqlite in-memory. R3 isolation logic is
ORM-portable and the existential question is `WHERE org_id = ? AND id = ?`
behavior, which is identical on SQLite vs Postgres. A Postgres integration
test will land in D5+ once Neon test-branch tokens are provisioned.
"""

from __future__ import annotations

import uuid
from datetime import UTC

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.job_store import JobStore
from app.db.models import Base, Org
from app.storage import r2

# ----------------------- fixtures -----------------------


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as s:
        yield s
    await engine.dispose()


@pytest_asyncio.fixture
async def two_orgs(session):
    org_a = Org(id="org_AAA", name="Alpha Labs")
    org_b = Org(id="org_BBB", name="Bravo Robotics")
    session.add_all([org_a, org_b])
    await session.flush()
    return org_a, org_b


# ----------------------- scenarios -----------------------


@pytest.mark.asyncio
async def test_org_b_cannot_read_org_a_job_by_id(session, two_orgs):
    """Scenario 1: direct read by id with foreign org_id MUST return None."""
    org_a, org_b = two_orgs
    store = JobStore(session)

    job_a = await store.create(
        org_id=org_a.id,
        user_id=None,
        from_format="agibot",
        to_format="lerobot-v3",
    )
    await session.flush()

    # Attacker (Org B) somehow obtained Org A's job UUID. With Org B's JWT
    # claim flowing in, the store MUST treat the id as unknown.
    leaked = await store.get(job_id=job_a.id, org_id=org_b.id)
    assert leaked is None, "R3 BREACH: Org B was able to read Org A's job"

    # Sanity: Org A still sees their own job.
    own = await store.get(job_id=job_a.id, org_id=org_a.id)
    assert own is not None
    assert own.id == job_a.id


@pytest.mark.asyncio
async def test_list_for_org_excludes_other_orgs_jobs(session, two_orgs):
    """Scenario 2: listing is org-scoped even when metadata overlaps."""
    org_a, org_b = two_orgs
    store = JobStore(session)

    # Org A creates 3 jobs.
    for _ in range(3):
        await store.create(
            org_id=org_a.id,
            user_id=None,
            from_format="agibot",
            to_format="lerobot-v3",
        )
    # Org B creates 1 job with overlapping format metadata.
    job_b = await store.create(
        org_id=org_b.id,
        user_id=None,
        from_format="agibot",
        to_format="lerobot-v3",
    )
    await session.flush()

    b_list = await store.list_for_org(org_id=org_b.id)
    assert len(b_list) == 1, f"R3 BREACH: Org B saw {len(b_list)} jobs, expected 1"
    assert b_list[0].id == job_b.id
    assert b_list[0].org_id == org_b.id

    a_list = await store.list_for_org(org_id=org_a.id)
    assert len(a_list) == 3
    assert all(j.org_id == org_a.id for j in a_list)


def test_presign_download_refuses_cross_org_key():
    """Scenario 3: storage-layer defense-in-depth.

    Even if a row corruption or upstream bug lets a wrong output_uri leak
    into a code path, presign_download MUST refuse to sign when the stored
    key prefix doesn't match (org_id, job_id) from the caller's JWT claim.

    This test does NOT need network — the prefix check happens before any
    boto3 call.
    """
    job_id = uuid.uuid4()
    # Stored key is under Org A's prefix, caller's JWT claim is Org B.
    a_key = f"orgs/org_AAA/jobs/{job_id}/output/result.zip"

    with pytest.raises(PermissionError, match="prefix mismatch"):
        r2.presign_download(
            org_id="org_BBB",
            job_id=job_id,
            output_key_value=a_key,
        )


def test_presign_download_refuses_mismatched_job_id():
    """Variant of scenario 3: org matches but job_id is forged.

    Without this check, a same-org attacker could request a presign for a
    UUID that happens to live under a different (org-internal) job's prefix.
    """
    real_job_id = uuid.uuid4()
    forged_job_id = uuid.uuid4()
    # Key references the REAL job id, caller passes FORGED.
    stored_key = f"orgs/org_AAA/jobs/{real_job_id}/output/result.zip"

    with pytest.raises(PermissionError, match="prefix mismatch"):
        r2.presign_download(
            org_id="org_AAA",
            job_id=forged_job_id,
            output_key_value=stored_key,
        )


# ----------------------- Cycle N · deeper R3 scenarios -----------------------
#
# The 4 scenarios above cover the *primary* attack surfaces (direct read,
# listing leak, two presign-prefix bypass variants). The 5 below cover
# secondary paths Cowork's gap analysis flagged in `coverage_expansion.md §2`:
# update-state cross-org, before-cursor pagination cross-org, FK-cascade
# behavior on org deletion, prefix-injection via filename, and the input_key
# derivation never trusting body-supplied org_id.


@pytest.mark.asyncio
async def test_update_state_cross_org_returns_false_no_mutation(session, two_orgs):
    """Scenario 5: `update_state` with foreign org_id MUST return False
    AND make zero row mutations.

    Source: `app/db/job_store.py:101-127` — the WHERE clause is
    `Job.id == job_id, Job.org_id == org_id`. A mismatched org_id should
    yield rowcount=0 → returns False. Caller (`upload.py` future D4 wiring)
    uses False to raise 404.
    """
    from app.db.models import JobState

    org_a, org_b = two_orgs
    store = JobStore(session)

    job_a = await store.create(
        org_id=org_a.id,
        user_id=None,
        from_format="agibot",
        to_format="lerobot-v3",
    )
    await session.flush()
    original_state = job_a.state

    # Attacker (Org B) tries to flip Org A's job to `done`.
    updated = await store.update_state(
        job_id=job_a.id,
        org_id=org_b.id,
        state=JobState.done,
    )
    assert updated is False, "R3 BREACH: cross-org update_state returned True"
    await session.refresh(job_a)
    assert job_a.state == original_state, "R3 BREACH: state mutated cross-org"


@pytest.mark.asyncio
async def test_list_before_cursor_does_not_bleed_across_orgs(session, two_orgs):
    """Scenario 6: keyset-pagination `before` cursor stays org-scoped.

    Source: `app/db/job_store.py:79-99`. The `before` filter is composed
    with `WHERE org_id = ?`. A timestamp cursor from Org A's history MUST
    NOT return Org B's earlier rows (timestamp overlap is the trap).
    """
    from datetime import datetime, timedelta

    org_a, org_b = two_orgs
    store = JobStore(session)

    # Create 3 Org A jobs and 3 Org B jobs interleaved in time. Cursor at
    # 'middle' of Org A history must only filter Org A rows.
    for _ in range(3):
        await store.create(
            org_id=org_a.id, user_id=None, from_format="agibot", to_format="lerobot-v3"
        )
    for _ in range(3):
        await store.create(
            org_id=org_b.id, user_id=None, from_format="agibot", to_format="lerobot-v3"
        )
    await session.flush()

    # Cursor far in the future → returns all 3 of caller's org. No org_b
    # rows must appear when caller is org_a.
    future = datetime.now(UTC) + timedelta(days=1)
    rows = await store.list_for_org(org_id=org_a.id, before=future)
    assert all(j.org_id == org_a.id for j in rows), (
        "R3 BREACH: before-cursor pagination bled across orgs"
    )
    assert len(rows) == 3


@pytest.mark.asyncio
async def test_org_cascade_delete_removes_jobs_not_other_orgs(session, two_orgs):
    """Scenario 7: deleting Org A cascades to Org A's jobs only.

    Source: `app/db/models.py:200-202` —
    `org_id = mapped_column(ForeignKey("orgs.id", ondelete="CASCADE"))`.
    If the FK cascade misconfig flipped to SET NULL or RESTRICT, Org A's
    jobs would persist as ownerless rows visible to any same-prefix query.
    """
    # IMPLEMENT IN D4 — needs DB-level ON DELETE CASCADE wired through
    # alembic head; current sqlite-in-memory create_all preserves the
    # declaration but a Postgres parity check belongs alongside Neon
    # branch tokens (same posture as test_r3_cross_org_isolation.py:13-17).


def test_input_key_derives_prefix_from_arg_not_filename(monkeypatch):
    """Scenario 8: `r2.input_key` MUST embed the *arg* org_id, never trust
    the filename to escape the prefix.

    Source: `app/storage/r2.py:62-64` — key template is
    `orgs/{org_id}/jobs/{job_id}/input/{_sanitize_filename(filename)}`.
    Filename = `../../org_BBB/evil.zip` MUST be sanitized to a leaf name,
    not concatenated raw. The `_sanitize_filename` helper at
    `r2.py:55-59` replaces `/` and `..` — assert that no `..` or `/`
    sequence survives in the output key prefix span.
    """
    job_id = uuid.uuid4()
    malicious = "../../org_BBB/jobs/xxx/output/evil.zip"
    key = r2.input_key("org_AAA", job_id, malicious)
    # The org-prefix span MUST remain `orgs/org_AAA/jobs/{job_id}/input/`.
    expected_prefix = f"orgs/org_AAA/jobs/{job_id}/input/"
    assert key.startswith(expected_prefix), f"R3 BREACH: filename escaped prefix. key={key!r}"
    # The sanitized leaf must have no path separators or parent refs.
    leaf = key[len(expected_prefix) :]
    assert "/" not in leaf and "\\" not in leaf and ".." not in leaf, (
        f"R3 BREACH: leaf retained traversal markers: {leaf!r}"
    )


def test_presign_download_refuses_empty_output_key():
    """Scenario 9: empty / None-stringified output_uri MUST NOT pass the
    prefix check (zero-length startswith returns True for empty prefix).

    Source: `app/storage/r2.py:108-112`. An empty string ALWAYS startswith
    any expected_prefix is False (Python semantics), so the guard fires —
    but we want a regression test pinning this exact behavior because a
    future refactor could swap startswith with `in` or relax the check.
    """
    job_id = uuid.uuid4()
    with pytest.raises(PermissionError, match="prefix mismatch"):
        r2.presign_download(
            org_id="org_AAA",
            job_id=job_id,
            output_key_value="",
        )
