# Integration + E2E Test Plan — Cycle P (D5+ harness)

**Cycle**: P · D5+ autonomous-drive
**Date**: 2026-05-14
**Author**: api-tester (Cowork)
**Status**: AUTHORITATIVE for D5 implementation. Today is D4-gated; this plan is built so that D5 can be a pure implementation pass against shipped D4 surfaces.
**Verification posture**: every claim cites `file_path:line_range` or URL+access-date.

---

## 1. Test layer taxonomy

Three test layers; boundaries are concrete.

- **Unit (87 tests, SHIPPED)** — single-module behavior with mocks at the module boundary, all under `tests/test_*.py`. Count verified per `coverage_expansion.md` §3.6: 55 baseline (Cycle G) + 32 expansions (Cycle N). 32 of these are `# IMPLEMENT IN D4` stub bodies. Scope: subprocess parsing, FSM updates, JobStore CRUD, R2 key derivation, schema validation, signature verification, JWT decode. No cross-module flows.

- **Integration (THIS PLAN)** — cross-module flows within the backend process. Boundary: ASGI app + DB + mocked vendors. Catches bugs at module-composition seams: e.g. does Clerk JWT middleware actually inject `request.state.org_id` such that downstream `app/api/upload.py` reads it through `httpx.AsyncClient(app=...)`? Does a body field override `request.state` (cross-brief amendment #6 — must NOT)? Does webhook exemption from JWT middleware fire at route resolution?

- **E2E (THIS PLAN)** — full user journey from signup through paid conversion. Boundary: full ASGI stack + DB + mocked vendor APIs + REAL `embodied-data` subprocess (Linux-gated per `tests/test_integration_beta.py:1-30` posture). Asserts user-visible outcomes (job downloadable, email received, tier flipped), not implementation seams.

---

## 2. Integration test harness

Implemented under `tests/e2e/conftest.py` (file exists, body marked `# IMPLEMENT IN D5`). Reuses the established sqlite-in-memory + `httpx.AsyncClient` pattern verified at `tests/test_r3_cross_org_isolation.py:34-42`.

**Stack:**
- pytest 8.x + pytest-asyncio (asyncio_mode = "auto" already set at `pyproject.toml:46`).
- `httpx.AsyncClient(app=app, base_url="http://test")` for ASGI-direct calls. No uvicorn, no localhost socket. Per `coverage_expansion.md` §5 open question — async middleware (Clerk JWT) likely forces AsyncClient over sync `TestClient`. Default to AsyncClient throughout E2E for consistency.
- sqlalchemy 2.0 async + aiosqlite in-memory engine. Identical fixture shape to `tests/test_r3_cross_org_isolation.py:34-51`. Postgres-parity tests deferred to D5+ alongside Neon test-branch tokens (same posture documented at `tests/test_r3_cross_org_isolation.py:13-17` and reused at `tests/test_stripe_webhook_idempotency.py:22-25`).
- moto >= 5.0 (S3 / R2 fake, already prescribed in `coverage_expansion.md` §4.4). Patched into `app.storage.r2._client()` via `monkeypatch.setenv("R2_ENDPOINT", moto_endpoint)`. Pre-Cloudflare R2 reference: https://developers.cloudflare.com/r2/api/s3/api/ (accessed 2026-05-14) confirms S3-compatible API surface.
- `stripe>=8.0,<9.0` (already prescribed in `app/api/stripe_webhook_spec.md` §6.4) — `stripe.Webhook.construct_event` used for real signature round-trip on the test side; `unittest.mock.patch` on `stripe.Subscription.retrieve` / `stripe.Invoice.retrieve` / `stripe.Customer.create` / `stripe.checkout.Session.create` for outbound calls.
- `python-jose[cryptography]>=3.3` + `respx>=0.20` (per `coverage_expansion.md` §4.4) for Clerk JWT mint + JWKS endpoint mock.
- `freezegun>=1.5` (already in `pyproject.toml:36`) for time-travel through Stripe signature tolerance and current_period_end.

**Scope boundary (what's IN integration vs unit):** if a test mounts the full FastAPI app and routes an HTTP request through middleware, it's integration. If it imports a function and calls it directly, it's unit.

---

## 3. E2E happy path (canonical journey)

Launch-readiness story (10 steps):

1. **Signup** — test mints Clerk JWT locally with `(sub, org_id)` claims; middleware accepts.
2. **POST /api/v1/jobs/presign-upload** -> `{upload_url, job_id}` with key prefix `orgs/{org_id}/jobs/{job_id}/input/{sanitized_filename}` per `app/storage/r2.py:62-64`.
3. **PUT** Beta-675 fixture bytes (`tests/fixtures/agibot_beta_675_single_ep/`, committed per `tests/conftest.py:24-31`) to moto presigned URL.
4. **POST /api/v1/jobs** -> kicks off `embodied-data convert ... --max-episodes 1` (free tier DR-018 #1, enforced D4 Story #8).
5. **GET /api/v1/jobs/{job_id}** polled every 2s (cross-brief #2). Transitions pending -> running -> done. Subprocess REAL on Linux; `@pytest.mark.skipif` on non-Linux (mirrors `test_integration_beta.py:1-30`).
6. **GET /api/v1/jobs/{job_id}/presign-download** -> SigV4 URL, 5-min TTL (`r2.py:24` `DOWNLOAD_TTL_S`).
7. **GET** the presigned URL -> retrieves LeRobot v3 output zip with canonical layout.
8. **POST /api/v1/billing/checkout** -> Stripe Checkout URL (mocked).
9. **Webhook delivery (simulated)** — test signs `customer.subscription.created` (Solo tier_id) and POSTs to `/api/v1/billing/webhook`. Handler verifies signature (spec §3.2), inserts `stripe_events` row (§1), re-fetches subscription (§4.1, mocked), flips `orgs.tier=solo`.
10. **Resend mocked** — `job-complete.md` called once (cross-brief #5).

The 5 files in `tests/e2e/` partition this journey into independently-runnable cells covering `dispatches/D4_specs.md` §8 launch-readiness criteria.

---

## 4. E2E test stub files (5 created under `tests/e2e/`)

Bodies marked `# IMPLEMENT IN D5`. Each stub cites underlying spec source + D4 dispatch dependency.

| File | Tests | Pins |
|---|---|---|
| `test_e2e_signup_to_first_conversion.py` | 2 | free-tier 1-ep round-trip + unauth 401 |
| `test_e2e_free_to_paid_upgrade.py` | 3 | soft-cap 402 + checkout-to-webhook-to-tier-flip + duplicate redelivery no-op |
| `test_e2e_failed_conversion_path.py` | 3 | converter_rejected envelope + oom (Linux-gated) + timeout + email |
| `test_e2e_org_isolation_under_load.py` | 4 | concurrent two-org isolation + 404-not-403 + body-injection ignored + Sentry breach breadcrumb |
| `test_e2e_stripe_subscription_lifecycle.py` | 4 | full ordered lifecycle + grace-window access + deterministic Idempotency-Key + dunning posture |

**Total: 16 E2E test stubs.** The 4 in `test_e2e_org_isolation_under_load.py` are particularly load-bearing — R3 is the EXISTENTIAL risk per architect tech_spec §6, and unit-layer R3 tests cover the store function but not the HTTP/middleware seam.

---

## 5. Mocking strategy

Discipline: mock at the vendor edge, not at internal module seams.

**Mocked (zero network):**
- **Clerk JWT verify** — local RSA keypair mints test JWTs; `respx` serves a JWKS endpoint matching the public key. Backend middleware reads `CLERK_JWKS_URL` (set to the respx-bound URL by the test). Ref: https://clerk.com/docs/backend-requests/handling/manual-jwt (accessed 2026-05-14).
- **R2** — moto S3 backend; `R2_ENDPOINT` env points at moto; bucket `agibridge-test`. Ref: https://docs.getmoto.org/en/latest/docs/services/s3.html (accessed 2026-05-14).
- **Stripe outbound API** — `stripe.Customer.create`, `Subscription.retrieve`, `Invoice.retrieve`, `checkout.Session.create`, `billing_portal.Session.create` all patched. Webhook signature creation on the test side is REAL via `stripe.Webhook.construct_event` keyed by `STRIPE_WEBHOOK_SECRET`.
- **Resend** — `resend.Emails.send` patched; tests assert call count + template_id. Ref: https://resend.com/docs (accessed 2026-05-14).
- **Sentry** — `sentry_sdk.capture_message` / `capture_exception` patched for R3-breach breadcrumb assertions.

**Real (in-process):**
- **FastAPI app** mounted into `httpx.AsyncClient(app=app)`.
- **SQLAlchemy + aiosqlite** in-memory engine with `Base.metadata.create_all` (mirrors `tests/test_r3_cross_org_isolation.py:36-38`).
- **`embodied-data==0.3.1` subprocess** on Linux runners with the CLI on PATH; skip on macOS arm64 + Windows. Version pin per `pyproject.toml:11`; depends on `--max-episodes N` flag stability.
- **JWT verify** — real `python-jose` round-trip against the locally-minted keypair.
- **Stripe HMAC verify** — real `stripe.Webhook.construct_event` round-trip on the handler side.

**[DECISION NEEDED]** None new. Postgres-vs-sqlite open-question from `coverage_expansion.md` §5 already resolved for D5: sqlite for D5 launch, Postgres parity deferred alongside Neon test-branch tokens (posture consistent across all three R3-isolation-aware test files).

---

## 6. CI integration

E2E tests SKIP in `.github/workflows/pre-merge-check.yml` (verified to exist; mock-merge gate per `feedback_pre_merge_integration_test`). E2E wall-clock is bounded by the real `embodied-data` subprocess (~30s for Beta-675 fixture) + moto/JWT overhead — too slow for the per-PR fast-feedback gate.

**New `.github/workflows/nightly-e2e.yml`:**
- Trigger: `schedule: cron: '0 7 * * *'` + `workflow_dispatch`.
- Runner: `ubuntu-latest` (Linux required for subprocess path; signal tests already Linux-gated at `tests/test_subprocess_runner.py:173`).
- Steps: setup-uv + install dev deps + `embodied-data==0.3.1` from PyPI + `pytest tests/e2e/ -v --timeout=600`.

**New `.github/workflows/e2e-on-label.yml`:** runs on `[e2e]` label OR PRs touching `app/api/billing.py`, `app/api/upload.py`, `app/api/download.py`, `app/db/job_store.py`, `app/storage/r2.py` (paths filter). `pre-merge-check.yml` itself unchanged — it inherits paths-filtered E2E via the new workflow rather than conflating gates.

---

## 7. D5 acceptance criteria (MVP-ready checklist)

D5 wave is COMPLETE when ALL boxes hit:

- [ ] D4 surfaces shipped + 87 unit tests green (D4 prerequisite, DR-018).
- [ ] 16 E2E stub bodies replaced with real assertions; `pytest tests/e2e/` passes on `ubuntu-latest` with zero unintended skips (Linux-gated tests excepted on macOS arm64).
- [ ] `pyproject.toml` dev-extras add `moto>=5.0`, `respx>=0.20`, `python-jose[cryptography]>=3.3` (per `coverage_expansion.md` §4.4); existing tight pins preserved.
- [ ] `nightly-e2e.yml` + `e2e-on-label.yml` merged with at least one green run each.
- [ ] Canonical happy-path test (`test_e2e_signup_to_first_conversion.py::test_free_tier_signup_then_one_episode_round_trip`) round-trips §3's 10 steps in < 60s wall-clock on `ubuntu-latest`.
- [ ] R3 HTTP-layer isolation pinned via `test_e2e_org_isolation_under_load.py` 4 scenarios — all green.
- [ ] Stripe lifecycle (created -> updated -> paid -> failed -> deleted) passes deterministically across 10 consecutive runs (no flakes).
- [ ] Failure-path asserts `embodied-data` `{error, suggestion}` envelope verbatim (DR-009 — no rewrite) + `job-failed.md` Resend template fires.
- [ ] No regression on 87 unit tests on Linux x86_64 + macOS arm64.
- [ ] This file updated with per-test -> spec-section verification matrix once stubs land (mirror `coverage_expansion.md §1` pattern).
- [ ] [DECISION NEEDED] surfaced: do failed jobs count toward soft cap? D5 default = yes (prevents OOM-retry abuse). Module-docstring in `test_e2e_failed_conversion_path.py` records the question.

When all hit -> Phase 1 MVP-ready -> Phase 2 launch push (`dispatches/D4_specs.md` §8).

---

## 8. Sources

**Project specs (verified):** `.plans/agibridge-2026/CLAUDE.md`; `DECISIONS.md` (DR-001 -> DR-018); `dispatches/D4_specs.md` §1/§3/§4/§8; `tests/coverage_expansion.md` §3/§4/§5; `app/api/stripe_webhook_spec.md` §1-§7; `tests/test_r3_cross_org_isolation.py:13-42`; `tests/test_stripe_webhook_idempotency.py:22-30`; `pyproject.toml:11, 30-39, 46`; `.github/workflows/pre-merge-check.yml` + `ci.yml` (verified to exist).

**External docs (all accessed 2026-05-14):** stripe webhooks/signatures/idempotent_requests + api/subscriptions/object; clerk.com/docs/backend-requests/handling/manual-jwt; developers.cloudflare.com/r2/api/s3/api; docs.getmoto.org S3; resend.com/docs; pypi.org/project/embodied-data (CLI pin); tanstack.com/query/latest (Cycle H polling cadence); docs.github.com Actions paths-filter.

**Standing rules applied:** `feedback_source_grounded_specs` (every claim cites file:line or URL+date); `feedback_no_padding_lists` (16 stubs verify-or-drop; race scenarios already pinned at unit layer NOT re-pinned); `feedback_anthropic_over_openai` (no LLM per DR-009); `feedback_pre_merge_integration_test` (pre-merge-check.yml inherits paths-filtered E2E, not modified directly).
