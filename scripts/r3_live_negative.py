#!/usr/bin/env python3
"""R3 cross-org isolation — LIVE negative probe (post-public-flip hard gate).

D4-harden Check 2. This is the gate the orchestrator fires IMMEDIATELY after
the HF Space is flipped PUBLIC. If it exits non-zero, the orchestrator
auto-rolls the Space back to private: a non-zero exit means a real tenant
got another tenant's job data over the live wire, which is the EXISTENTIAL
R3 risk (architect tech_spec §6) and is unacceptable on a public surface.

WHAT IT PROVES (live, end-to-end, real Clerk JWTs — NOT the sqlite unit
suite in tests/test_r3_cross_org_isolation.py):

  (i)   Org A creates a job   -> POST /api/v1/jobs/presign-upload
        (this mints a real org-A-scoped Job row + returns its job_id;
        no actual R2 upload is needed to obtain the id — see
        app/api/jobs.py:182-225).
  (ii)  Org B, with ITS OWN valid Clerk JWT, attempts:
          - GET /api/v1/jobs/{A_job_id}
          - GET /api/v1/jobs/{A_job_id}/presign-download
        Both MUST return 403 or 404 and MUST NOT return 200, MUST NOT
        return any field of A's job, MUST NOT return a presigned URL.
        The server returns 404 (not 403) by design so the existence of
        A's id in another org is not even confirmed (side-channel-safe,
        app/api/jobs.py:345-362, :365-394) — we accept BOTH 403 and 404.
  (iii) Sanity: Org A CAN read its own job (200) — proves the negative
        result in (ii) is real isolation, not a blanket 4xx (e.g. auth
        misconfig 401 for everyone) masquerading as "isolation".

NEGATIVE-CONTROL DISCIPLINE: a test that 404s for everyone (including the
owner) proves nothing. Step (iii) is the positive control that makes the
(ii) failures meaningful.

DEPENDENCIES: httpx + Python stdlib only. No app imports — this must run
against the LIVE deployed Space from anywhere (CI runner, laptop), not in
the app venv.

ENV (all required):
  R3_BASE_URL    e.g. https://allenwu06-agibridge.hf.space  (no trailing /)
  R3_JWT_ORG_A   a valid Clerk session JWT for org A
  R3_JWT_ORG_B   a valid Clerk session JWT for org B (DIFFERENT org)

EXIT CODES (documented contract — orchestrator branches on these):
  0  PASS  — A created a job, A can read it, B is denied (403/404) on
            BOTH the direct read AND the presign-download. Isolation holds.
  1  FAIL  — R3 BREACH: B received 200 and/or A's job data and/or a
            presigned URL for A's job. THE SPACE MUST BE ROLLED BACK.
  2  ERROR — could not run the probe to a conclusive result (missing env,
            network failure, A could not create a job, A could not read
            its own job [positive control failed], unexpected status).
            Treat as INCONCLUSIVE → do NOT certify the flip; investigate.

Usage:
  R3_BASE_URL=https://allenwu06-agibridge.hf.space \\
  R3_JWT_ORG_A=... R3_JWT_ORG_B=... \\
  python scripts/r3_live_negative.py
"""

from __future__ import annotations

import os
import sys

import httpx

# Exit codes (see module docstring contract).
EXIT_PASS = 0
EXIT_FAIL = 1
EXIT_ERROR = 2

# Per-request timeout. The Space cold-starts; presign-upload also creates a
# DB row. 30s is generous for a healthy Space and still fails fast if the
# Space is wedged (which is itself a flip-blocker the orchestrator handles).
_TIMEOUT = httpx.Timeout(30.0)

# A minimal valid presign-upload body. from/to must be a supported,
# non-identical pair (app/api/jobs.py:196-203, _SUPPORTED_PAIRS); no real
# upload follows so the filename is cosmetic.
_PRESIGN_BODY = {
    "filename": "r3-live-negative-probe.zip",
    "from_format": "agibot",
    "to_format": "lerobot-v3",
}


def _fail(msg: str) -> None:
    print(f"R3 LIVE NEGATIVE: FAIL — {msg}", file=sys.stderr)


def _err(msg: str) -> None:
    print(f"R3 LIVE NEGATIVE: ERROR (inconclusive) — {msg}", file=sys.stderr)


def _ok(msg: str) -> None:
    print(f"R3 LIVE NEGATIVE: {msg}")


def _bearer(jwt: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {jwt}", "Content-Type": "application/json"}


def main() -> int:
    base = (os.environ.get("R3_BASE_URL") or "").rstrip("/")
    jwt_a = os.environ.get("R3_JWT_ORG_A") or ""
    jwt_b = os.environ.get("R3_JWT_ORG_B") or ""
    if not base or not jwt_a or not jwt_b:
        _err("R3_BASE_URL, R3_JWT_ORG_A and R3_JWT_ORG_B must all be set.")
        return EXIT_ERROR
    if jwt_a == jwt_b:
        _err("R3_JWT_ORG_A == R3_JWT_ORG_B — the two JWTs must be DIFFERENT orgs.")
        return EXIT_ERROR

    try:
        with httpx.Client(base_url=base, timeout=_TIMEOUT) as c:
            # --- (i) Org A creates a job (presign-upload mints the row) ---
            r = c.post(
                "/api/v1/jobs/presign-upload",
                headers=_bearer(jwt_a),
                json=_PRESIGN_BODY,
            )
            if r.status_code != 201:
                _err(
                    f"Org A could not create a job: POST /jobs/presign-upload "
                    f"returned {r.status_code} (expected 201). Body: {r.text[:300]}"
                )
                return EXIT_ERROR
            try:
                a_job_id = r.json()["job_id"]
            except (ValueError, KeyError) as exc:
                _err(f"Org A presign-upload response missing job_id: {exc}. Body: {r.text[:300]}")
                return EXIT_ERROR
            if not a_job_id:
                _err("Org A presign-upload returned an empty job_id.")
                return EXIT_ERROR
            _ok(f"(i) Org A created job {a_job_id}.")

            # --- (iii) positive control FIRST: A must read its own job ---
            # Run the positive control before the negative so a blanket-4xx
            # misconfig (everything 404s) is caught as ERROR, never silently
            # passed as "isolation".
            r = c.get(f"/api/v1/jobs/{a_job_id}", headers=_bearer(jwt_a))
            if r.status_code != 200:
                _err(
                    f"Positive control FAILED: Org A could not read its OWN job "
                    f"{a_job_id} (GET returned {r.status_code}, expected 200). "
                    f"Cannot trust the isolation result — investigate auth/env."
                )
                return EXIT_ERROR
            _ok(f"(iii) Positive control OK: Org A reads its own job {a_job_id} (200).")

            # --- (ii) Org B attacks A's job_id on BOTH endpoints ---
            breaches: list[str] = []

            rb = c.get(f"/api/v1/jobs/{a_job_id}", headers=_bearer(jwt_b))
            if rb.status_code == 200:
                breaches.append(
                    f"GET /jobs/{a_job_id} returned 200 to Org B "
                    f"(R3 BREACH — body: {rb.text[:300]})"
                )
            elif rb.status_code not in (403, 404):
                # Not a confirmed breach, but not the expected deny either —
                # inconclusive, do not certify.
                _err(
                    f"GET /jobs/{a_job_id} as Org B returned unexpected "
                    f"{rb.status_code} (expected 403/404). Body: {rb.text[:300]}"
                )
                return EXIT_ERROR
            else:
                _ok(f"(ii.a) Org B denied on GET /jobs/{a_job_id} ({rb.status_code}).")

            rp = c.get(
                f"/api/v1/jobs/{a_job_id}/presign-download",
                headers=_bearer(jwt_b),
            )
            if rp.status_code == 200:
                breaches.append(
                    f"GET /jobs/{a_job_id}/presign-download returned 200 to Org B "
                    f"(R3 BREACH — a presigned URL for A's data was minted: "
                    f"{rp.text[:300]})"
                )
            elif rp.status_code not in (403, 404):
                _err(
                    f"GET /jobs/{a_job_id}/presign-download as Org B returned "
                    f"unexpected {rp.status_code} (expected 403/404). "
                    f"Body: {rp.text[:300]}"
                )
                return EXIT_ERROR
            else:
                _ok(
                    f"(ii.b) Org B denied on GET /jobs/{a_job_id}/presign-download "
                    f"({rp.status_code})."
                )

            if breaches:
                for b in breaches:
                    _fail(b)
                _fail("ROLL THE SPACE BACK TO PRIVATE. R3 isolation is broken.")
                return EXIT_FAIL

    except httpx.HTTPError as exc:
        _err(f"network/transport error talking to {base}: {exc!r}")
        return EXIT_ERROR

    _ok("PASS — R3 cross-org isolation holds on the live Space.")
    return EXIT_PASS


if __name__ == "__main__":
    sys.exit(main())
