"""E2E #3 — failed conversion (error envelope, email, tier untouched).

Customers will hit conversion failures (bad input, OOM, timeout). This
E2E pins that the full failure path stays clean: job state ends in
`failed`, the user-facing error envelope matches embodied-data's
`{error, suggestion}` shape (DR-009 reasoning — lib already returns
this at `_emit.py:32-46`), and a `job-failed.md` email fires
(cross-brief amendment #5).

Failure modes exercised:
  A. `converter_rejected_input` — malformed AgiBot tarball.
     Source: `app/api/subprocess_runner.py` map_outcome path covered by
     `tests/test_subprocess_runner.py::test_map_converter_rejected_input_passes_through_lib_message`.
  B. `oom_suspected` — SIGKILL with empty stdout (Linux-only).
     Source: `tests/test_subprocess_runner.py::test_map_oom_suspected_on_sigkill_no_payload`.
  C. `conversion_timeout` — wall-clock cap exceeded.
     Source: `tests/test_subprocess_runner.py::test_map_conversion_timeout`.

Cross-module surfaces:
  - Subprocess runner (unit-tested) integrated with JobStore.update_state
    + error-field whitelist (job_store.py:118-120 — pinned by
    `coverage_expansion.md` §3.5 test_update_state_with_unknown_field).
  - Resend email dispatch on terminal `failed` state.
  - Sentry breadcrumb (D4 Refactor #5) tagged with org_id but NOT with
    user-attributable strings (carry-forward F-1 discipline per
    `dispatches/D4_specs.md` §4 Refactor #5).

Mocking boundary:
  - Clerk JWT: local mint.
  - R2: moto.
  - embodied-data subprocess: REAL on Linux runner, with crafted input
    that triggers the targeted failure mode. Mode B (oom) gated with
    `@pytest.mark.skipif(platform.system() != "Linux")` mirroring
    `tests/test_subprocess_runner.py:173` posture.
  - Resend: patched `resend.Emails.send`.

Negative invariants pinned (these must NOT happen on failure):
  - orgs.tier MUST NOT change (failure is orthogonal to billing).
  - usage_events row for conversion_completed MUST NOT be inserted.
  - The user's job count toward soft cap MUST still increment OR not —
    [DECISION NEEDED] which one matches DR-018 #2 intent? Surface to
    orchestrator at synthesis. Default in D5 implementation: count
    failures toward cap to prevent OOM-retry abuse.
"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_malformed_input_yields_converter_rejected_envelope():
    """Failure mode A.

    Assertions:
      - Final job.state == 'failed'
      - job.error.code == 'converter_rejected_input'
      - job.error.suggestion is a non-empty string from lib's _emit.py
        verbatim (no rewrite)
      - Resend `job-failed.md` template called once
      - No download URL is presignable (download.py:42 yields 409 not_ready
        OR 410 download_expired per coverage_expansion.md §2.3)
    """
    # IMPLEMENT IN D5 — depends on D4 Refactor #3 (download.py wired to JobStore).


@pytest.mark.skipif(
    True,  # IMPLEMENT IN D5 — gate on platform.system() != 'Linux'
    reason="OOM path requires Linux process-group semantics",
)
@pytest.mark.asyncio
async def test_oom_killed_yields_oom_suspected_envelope():
    """Failure mode B. SIGKILL + empty stdout."""
    # IMPLEMENT IN D5 — needs a memory-blowup fixture or RLIMIT_AS injection.


@pytest.mark.asyncio
async def test_conversion_timeout_yields_failed_state_and_email():
    """Failure mode C. Wall-clock cap exceeded.

    Use a fixture that simulates a >timeout subprocess via monkeypatch on
    the subprocess runner timeout constant (LOW invasiveness — does not
    require modifying embodied-data lib, which is in maintenance mode
    per `project_embodied_data`).
    """
    # IMPLEMENT IN D5 — depends on D4 Refactor #3 + email dispatch.
