"""E2E #1 — signup -> first successful conversion (free-tier preview).

Pins the canonical happy path called out in `dispatches/D4_specs.md` §8
"E2E happy path" acceptance criteria: signup -> upload -> conversion ->
download in under 30 min user-time. This file's posture is the *free*
preview half of that flow (1-episode cap, no Stripe interaction).

Free-tier semantics: per DR-018 default #1, NO trial. Free = 1-episode
preview enforced by `--max-episodes=1` on the embodied-data subprocess
(`dispatches/D4_specs.md` §4 Story #8). Soft cap 5/mo per DR-018 #2.

Cross-module surfaces touched:
  - Clerk JWT middleware (D4 Story #2)            — auth
  - JobStore.create / get / update_state         — DB (D1-D3 shipped)
  - r2.input_key / output_key / presign_*        — storage (D1-D3 shipped)
  - app/api/upload.py refactored                  — D4 Refactor #3
  - app/api/status.py refactored                  — D4 Refactor #3
  - app/api/download.py refactored                — D4 Refactor #3
  - app/api/subprocess_runner.py (existing 14-test coverage)

Mocking boundary:
  - Clerk JWT: minted locally via test RSA keypair fixture
    (`coverage_expansion.md` §4.2 `test_keypair`).
  - R2: moto S3 backend (`coverage_expansion.md` §4.3 `moto>=5.0`).
  - Stripe: NOT touched on this path (free tier).
  - embodied-data subprocess: REAL on Linux runners; gated with
    `@pytest.mark.skipif` when the CLI is not on PATH (mirrors
    `tests/test_integration_beta.py:1-30` posture).

Spec sources:
  - `dispatches/D4_specs.md` §8 — E2E happy path acceptance checklist
  - `dispatches/D4_specs.md` §3 amendment #3 — 5 MVP endpoint shapes
  - `frontend/D4_rehydration_spec.md` §3 — endpoint contracts (authoritative)
  - `coverage_expansion.md` §1.7 — `test_integration_beta.py` pattern
"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_free_tier_signup_then_one_episode_round_trip():
    """Full FSM walk under free-tier semantics.

    Given: a fresh Clerk user + org just signed up.
    When:  user uploads an AgiBot Beta task fixture (≤1 episode) and polls.
    Then:  job transitions pending -> running -> done within timeout,
           download URL is presigned and downloadable, and a
           `usage_events` row is recorded for soft-cap accounting.

    Assertions to pin in D5:
      1. POST /api/v1/jobs/presign-upload returns 200 with `upload_url`,
         `job_id`, and key prefix containing `orgs/{org_id}/jobs/{job_id}/input/`.
      2. PUT to moto presigned URL with the Beta-675 fixture bytes returns 200.
      3. POST /api/v1/jobs notifies backend; response includes job_id and state=pending.
      4. GET /api/v1/jobs/{job_id} polled every 2s (cross-brief #2 cadence)
         transitions pending -> running -> done in <= 30 min wall-clock.
      5. The subprocess invocation included `--max-episodes 1`
         (D4 Story #8 free-tier gate).
      6. GET /api/v1/jobs/{job_id}/presign-download returns a SigV4 URL with
         a 5-min TTL (matches r2.py:24 `DOWNLOAD_TTL_S`).
      7. GET against the presigned download URL retrieves the LeRobot v3
         output zip with the canonical file layout.
      8. A `usage_events` row exists with type=conversion_completed,
         org_id=request.state.org_id.
    """
    # IMPLEMENT IN D5 — depends on D4 Story #2 (Clerk middleware), Story #8
    # (tier gating), and Refactors #3 (route -> JobStore + JWT org_id).


@pytest.mark.asyncio
async def test_free_tier_unauth_request_returns_401():
    """Negative-path E2E: same flow without `Authorization: Bearer <JWT>`
    must 401 at the middleware (D4 Story #2), never reaching JobStore.

    Source: `dispatches/D4_specs.md` §4 Story #2 acceptance criteria.
    """
    # IMPLEMENT IN D5 — depends on D4 Story #2.
