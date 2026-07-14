"""E2E #4 — R3 cross-org isolation invariants under concurrent load.

R3 is the EXISTENTIAL risk per architect tech_spec §6. Unit-layer
coverage at `tests/test_r3_cross_org_isolation.py` (9 tests) pins the
single-request behavior of JobStore + r2 presign refusal. This E2E
layer pins that the invariants hold under realistic concurrent traffic
through the full FastAPI app with JWT middleware extracting org_id.

The cross-brief amendment #6 in `dispatches/D4_specs.md` is
NON-OPTIONAL: every R2 prefix derives from `request.state.org_id` (set
by JWT middleware), NEVER from request body. This E2E checks an
attacker cannot supply a body-org override AND that concurrent
deliveries from two orgs never bleed.

Scenarios:
  1. Two orgs (A, B), 10 concurrent job creates each, interleaved.
     Final assertion: `JobStore.list_for_org(org_a)` returns exactly
     10 jobs all of which have `org_id == org_a.id`, and symmetric for B.
     Pins `coverage_expansion.md` §2.1 untested paths (list with cursor
     under concurrent writes — sqlite serializes, but the test still
     pins the contract).
  2. Org B attempts to download Org A's output by guessing a job_id
     (uuid4 collision is cryptographically negligible; the test forges
     a known job_id via DB read with admin claim, then calls
     `GET /api/v1/jobs/{a_job_id}/presign-download` with Org B's JWT).
     Expected: 404 (NOT 403) per
     `tests/test_r3_cross_org_isolation.py:1-12` (side-channel hygiene).
  3. Body-injection: Org B POSTs /api/v1/jobs with a body containing
     `org_id: "org_A"`. Backend MUST ignore the body field and use
     `request.state.org_id` (cross-brief #6). The created job's R2
     prefix MUST be `orgs/org_B/...`.
  4. Sentry breadcrumb on R3-isolation-breach attempt:
     `dispatches/D4_specs.md` §3 amendment #6 + Cycle L spec require a
     Sentry alert hook on prefix mismatch. E2E pins the
     `sentry_sdk.capture_message` call fires with `r3.isolation.breach`
     tag when scenario 3 triggers (patched capture for assertion).

Why E2E (vs unit): the unit tests pin JobStore behavior in isolation.
The E2E variant pins (a) middleware -> handler -> store wiring is
correct, (b) FastAPI request body parsing doesn't accidentally override
request.state, (c) the prefix-mismatch defense fires through the actual
HTTP layer not just direct r2.py calls.

Mocking boundary:
  - Clerk JWT: 2 distinct mints (org_A, org_B).
  - R2: moto with both orgs' prefixes pre-seeded.
  - Sentry: `unittest.mock.patch("sentry_sdk.capture_message")`.

Concurrency note: sqlite-in-memory serializes writes (single connection
posture). True parallel-write R3 race is deferred to D5+ Postgres
parity per `tests/test_r3_cross_org_isolation.py:13-17` + Cycle G spec
§2.3 final paragraph. This E2E pins the FastAPI/middleware/JobStore
wiring under interleaved-async load (semantically equivalent under
asyncio cooperative scheduling).
"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_concurrent_two_org_writes_no_list_bleed():
    """Scenario 1 — interleaved creates, list_for_org stays scoped."""
    # IMPLEMENT IN D5 — depends on D4 Refactor #3 (POST /jobs -> JobStore.create).


@pytest.mark.asyncio
async def test_cross_org_presign_download_returns_404_not_403():
    """Scenario 2 — 404 not 403 (side-channel hygiene).

    `tests/test_r3_cross_org_isolation.py:1-12` is the canonical comment
    on this: returning 403 would leak the existence of the id in
    another org. This E2E pins that the HTTP response code stays 404.
    """
    # IMPLEMENT IN D5 — depends on D4 Refactor #3 (download.py JWT-aware).


@pytest.mark.asyncio
async def test_body_injection_org_id_is_ignored():
    """Scenario 3 — body org_id override MUST be ignored.

    Cross-brief #6 is the non-optional constraint this test pins. If
    the regression bug ever lands (e.g. a future refactor switches to a
    Pydantic body with an `org_id` field and the handler doesn't strip
    it), this test fires immediately.
    """
    # IMPLEMENT IN D5 — depends on D4 Story #2 + Refactor #3.


@pytest.mark.asyncio
async def test_isolation_breach_attempt_emits_sentry_breadcrumb():
    """Scenario 4 — Sentry alert hook fires on prefix-mismatch.

    Per Cycle L Sentry spec (R3-isolation-breach + R2-presign-mismatch
    hooked alerts, referenced in `dispatches/D4_specs.md` §3 amendment #6).
    """
    # IMPLEMENT IN D5 — depends on D4 Refactor #5 (Sentry SDK init).
