# agibridge Python SDK — design spec

**Status**: DRAFT (contract). Implementation deferred to D5+ feature wave.
**Author**: Cycle AA Wave 3 (Developer Advocate, orchestrator-direct)
**Date**: 2026-05-14
**Audience**: PhD students batch-converting datasets, robotics teams CI-integrating conversion, AV teams scripting against the API.
**Voice anchor**: terse, source-cited, no marketing exclamation — inherits `marketing/site-copy/homepage.md` Cycle X profile.

This spec defines the contract for the `agibridge` Python SDK. Backend D4 (`dispatches/D4_specs.md` §4) does NOT implement API keys — D4 ships Clerk JWT only. The API key model below is the contract; D5+ adds the issuance/validation backend.

---

## 1. Package name + availability

**Recommended PyPI name**: `agibridge`.

**Availability verified 2026-05-14**:
- `https://pypi.org/pypi/agibridge/json` → HTTP 404 (available)
- `https://pypi.org/pypi/embodied-data/json` → HTTP 200 (`embodied-data` v0.3.1 GA already owned by Allen per `DECISIONS.md` charter §Mission)

**Namespace collision risk**: low. `embodied-data` is the underlying library distinct from `agibridge` SaaS wrapper. Both names can coexist as separate PyPI distributions owned by the same maintainer. The SDK installs as `pip install agibridge` and imports as `import agibridge`; no module-name overlap with `import embodied_data`.

**Fallbacks if `agibridge` is grabbed before reservation** (also verified 2026-05-14):
- `agibridge-sdk` (HTTP 404 — available)
- `agibridge-py` (HTTP 404 — available)
- `agibridge-client` (recommended fallback over `-py` since `-sdk` is the Stripe convention `stripe-python` and Clerk's `clerk-backend-api` style)

**Action for orchestrator**: reserve `agibridge` on PyPI before D5 ships. Empty 0.0.0-dev0 placeholder is acceptable (Stripe Python SDK uses the same pre-reservation pattern per `https://pypi.org/project/stripe/`, accessed 2026-05-14).

---

## 2. Authentication model

**Two auth methods, one for each user surface**:

| Surface | Auth method | Token format | Issued by |
|---|---|---|---|
| Web UI (browser) | Clerk JWT | Short-lived JWT, auto-rotated | Clerk (per DR-005) |
| SDK / programmatic | API key | `agibridge_pk_<base64>` / `agibridge_sk_<base64>` | agibridge dashboard (D5+) |

**API key format rationale**:
- `agibridge_pk_<base64>` = publishable key, safe to embed in browser bundle for read-only flows (e.g. job-status polling on a customer's dashboard). Modeled on Clerk's `pk_test_...` / `pk_live_...` and Stripe's `pk_live_...` (verified `https://stripe.com/docs/keys`, accessed 2026-05-14).
- `agibridge_sk_<base64>` = secret key, server-side only, never bundled. Modeled on Stripe `sk_live_...` and Clerk `sk_test_...` (`https://clerk.com/docs/references/backend/overview`, accessed 2026-05-14).

**Key prefix + length**: `agibridge_sk_` followed by 32 bytes of `secrets.token_urlsafe()` → total ~56 chars. Prefix is human-readable; the prefix-search pattern allows GitHub's secret-scanning to detect leaked keys (Stripe documented this pattern at `https://stripe.com/docs/security#secret-scanning`, accessed 2026-05-14 — recommend agibridge register the prefix with GitHub Secret Scanning Partner Program once first paying customer lands).

**Authorization header**: `Authorization: Bearer <agibridge_sk_...>`. Same header shape as Clerk JWT — backend middleware (D5+) distinguishes by inspecting the token: JWT decodes to a Clerk-issued claim set, API key matches `^agibridge_(pk|sk)_` prefix and looks up in `api_keys` table (org-scoped, hashed at rest with `argon2id`).

**Org scoping**: every API key is bound to exactly one Clerk Organization at creation time. Backend cross-org isolation invariant from `D4_specs.md` §3 amendment #6 applies identically: `org_id` derives from the key lookup, never from request body.

**Rotation**: dashboard (D5+) supports rotating keys with 24-hour grace window — old key returns 200 with `X-Agibridge-Key-Deprecated: <iso8601-expiry>` header during grace. Modeled on Stripe rolling keys (`https://stripe.com/docs/keys#rolling-keys`, accessed 2026-05-14).

---

## 3. Idiomatic Python surface

**Decision**: ship both sync and async clients. Justification:
- PhD student batch scripts run sync inside Jupyter / `python script.py`. Async noise hurts beginner UX.
- CI integrations and robotics-pipeline orchestrators (Prefect, Dagster, Ray) consume async. Forcing them to `asyncio.run(...)` per call defeats the purpose.
- Stripe Python ships sync-first with async via `await stripe.Customer.create_async(...)` (`https://stripe.com/docs/api?lang=python` accessed 2026-05-14). Clerk's Python SDK (`clerk-backend-api` 5.0.6) ships async-only and forces sync users to `asyncio.run`. Stripe's pattern is friendlier; we adopt it.

**Implementation**: both clients wrap `httpx`. `httpx.Client` for sync, `httpx.AsyncClient` for async (`https://www.python-httpx.org/`, accessed 2026-05-14, latest 0.28.1). Single transport library keeps timeout / retry / connection-pool config consistent.

### 3.1 Sync usage

```python
from agibridge import Agibridge

client = Agibridge(api_key="agibridge_sk_...")  # or pulls from $AGIBRIDGE_API_KEY

# 1. Presign upload
presign = client.jobs.presign_upload(
    filename="agibot_world_episodes_0_to_99.tar.gz",
    size_bytes=2_400_000_000,
)

# 2. Upload bytes directly to R2 (helper bundled with SDK)
client.jobs.upload(presign, file_path="./agibot_world_episodes_0_to_99.tar.gz")

# 3. Notify backend upload complete → conversion kicks off
job = client.jobs.start(job_id=presign.job_id)

# 4. Poll until terminal state (helper blocks; raises AgibridgeJobFailedError on failure)
job = client.jobs.wait_until_done(job_id=presign.job_id, timeout=1800)

# 5. Presign download + stream to disk
download = client.jobs.presign_download(job_id=job.id)
client.jobs.download(download, dest_path="./lerobot_v3_output.tar.gz")
```

### 3.2 Async usage

```python
import asyncio
from agibridge import AsyncAgibridge

async def main():
    async with AsyncAgibridge(api_key="agibridge_sk_...") as client:
        presign = await client.jobs.presign_upload(
            filename="dataset.tar.gz",
            size_bytes=2_400_000_000,
        )
        await client.jobs.upload(presign, file_path="./dataset.tar.gz")
        await client.jobs.start(job_id=presign.job_id)
        job = await client.jobs.wait_until_done(job_id=presign.job_id)
        download = await client.jobs.presign_download(job_id=job.id)
        await client.jobs.download(download, dest_path="./out.tar.gz")

asyncio.run(main())
```

### 3.3 Endpoint surface

Matches `D4_specs.md` §3 amendment #3 — 5 MVP endpoints authoritative for both backend and frontend; SDK matches exactly.

| API endpoint | SDK method | Returns |
|---|---|---|
| `POST /api/v1/jobs/presign-upload` | `client.jobs.presign_upload(filename, size_bytes)` | `PresignUploadResponse` |
| `POST /api/v1/jobs` | `client.jobs.start(job_id)` | `Job` |
| `GET /api/v1/jobs` | `client.jobs.list(cursor=None, limit=50)` | `JobList` (paginated) |
| `GET /api/v1/jobs/{job_id}` | `client.jobs.get(job_id)` | `Job` |
| `GET /api/v1/jobs/{job_id}/presign-download` | `client.jobs.presign_download(job_id)` | `PresignDownloadResponse` |

**Bundled helpers (not API endpoints, SDK-side conveniences)**:
- `client.jobs.upload(presign, file_path | file_obj)` — PUTs bytes to R2 with `tqdm` progress bar (optional dep, falls back silently)
- `client.jobs.download(presign, dest_path | file_obj)` — GETs bytes from R2 with `tqdm` progress
- `client.jobs.wait_until_done(job_id, timeout, poll_interval=5.0)` — polls `GET /jobs/{id}` until terminal state; respects `D4_specs.md` §3 amendment #2 rate-limit budget (30 req/min/user) with 5 s default interval

---

## 4. Type definitions — auto-gen from OpenAPI

**Recommendation**: `datamodel-code-generator` (not `openapi-python-client`).

**Reasoning**:
- `openapi-python-client` 0.28.4 (verified PyPI 2026-05-14) generates a full client including HTTP transport — duplicates work we want to own (httpx wiring, error envelope parsing, key auth header). Output is opinionated and hard to customize.
- `datamodel-code-generator` generates Pydantic v2 models only — exactly what we want. We write the transport layer; codegen owns the types. (`https://pypi.org/project/datamodel-code-generator/`, accessed 2026-05-14)
- Pydantic v2 (not v1) is the right call: FastAPI 0.110+ is Pydantic v2 native, and `embodied-data` already uses Pydantic v2 per the existing backend at `app/api/upload.py`.

**Build step** (added to `pyproject.toml`):

```toml
[tool.datamodel-codegen]
input = "../app/openapi.json"
output = "src/agibridge/_types.py"
output-model-type = "pydantic_v2.BaseModel"
target-python-version = "3.10"
```

CI runs `datamodel-codegen` on every backend OpenAPI change; if `_types.py` diff is non-empty, SDK version auto-bumps minor. Generated file checked in for reproducibility (deviates from "generate at install" — but reviewers can read the diff in PRs).

---

## 5. Error handling

Per-endpoint exception classes inheriting `AgibridgeError`. Modeled on Stripe Python (`https://stripe.com/docs/api/errors?lang=python`, accessed 2026-05-14):

```python
class AgibridgeError(Exception):
    """Base. All SDK errors inherit this."""
    status_code: int | None
    code: str | None       # e.g. "rate_limit_exceeded"
    message: str
    suggestion: str | None # FastAPI envelope: {detail: {code, message, suggestion}}
    request_id: str | None # backend X-Request-ID for support tickets

class AgibridgeAPIError(AgibridgeError):
    """5xx server errors. Retried automatically (see §6)."""

class AgibridgeAuthError(AgibridgeError):
    """401 / 403. Bad API key, expired, or org-scope mismatch."""

class AgibridgeRateLimitError(AgibridgeError):
    """429. Caller hit the per-org budget. Honor Retry-After header."""
    retry_after: float  # seconds

class AgibridgeNotFoundError(AgibridgeError):
    """404. job_id doesn't exist OR belongs to a different org (no info leak)."""

class AgibridgeValidationError(AgibridgeError):
    """400 / 422. Input shape rejected by FastAPI / app layer."""

class AgibridgeJobFailedError(AgibridgeError):
    """Raised by wait_until_done when job reaches status='failed'.
    Includes job.error envelope (code, message, suggestion)."""
    job: Job

class AgibridgeTimeoutError(AgibridgeError):
    """wait_until_done exceeded timeout. job is still running server-side."""
    job_id: str
```

**Envelope parsing**: backend returns FastAPI `{detail: {code, message, suggestion}}` per `embodied-data` lib (`_emit.py:32-46` per `DECISIONS.md` DR-009 reasoning). SDK preserves all three fields; never collapses `suggestion` into `message`.

**Why not Result types**: Pythonic convention is exceptions. Result-monad-style (returning `Ok(T) | Err(E)`) is fashionable in Rust but reads as un-Pythonic to the target audience (PhD students who've never imported `returns.result`). Stripe + Anthropic + OpenAI all use exceptions; SDK matches.

---

## 6. Retry + timeout policy

**Defaults**:
- Per-request timeout: 30 s connect, 60 s read (httpx `httpx.Timeout(connect=30, read=60)`)
- 429 (rate limit) → retry up to 5 times with `Retry-After` header honored; if missing, exponential `2^n + jitter` seconds (1, 2, 4, 8, 16)
- 5xx (server error) → retry up to 3 times exponential `2^n + jitter`
- Connection errors (DNS, TCP reset, TLS handshake) → retry up to 3 times exponential
- 4xx (other than 429) → no retry (input bug, not a transient failure)

**Override pattern** (Stripe-style — `https://stripe.com/docs/api/usage/configuration?lang=python` accessed 2026-05-14):

```python
client = Agibridge(
    api_key="...",
    max_retries=0,                       # disable retries entirely
    timeout=httpx.Timeout(connect=10, read=300),  # custom for long uploads
)

# Or per-call
job = client.jobs.get(job_id="...", timeout=5.0, max_retries=1)
```

**Backoff library**: implement inline (don't pull `tenacity` or `backoff` as deps — keep transitive deps minimal). httpx 0.28 ships `httpx_retries` plugin but it's experimental; SDK rolls its own ~30-line retry helper.

---

## 7. Pagination

`client.jobs.list()` returns a `JobList` object that is iterable AND exposes raw cursor metadata:

```python
# Auto-iteration over all jobs (most common — backend cursor opaque)
for job in client.jobs.list():
    print(job.id, job.status)

# Manual page-at-a-time control (CI scripts processing N jobs/batch)
page = client.jobs.list(limit=50)
while page.has_more:
    for job in page.data:
        process(job)
    page = client.jobs.list(cursor=page.next_cursor)
```

**Implementation**: `JobList.__iter__` is a generator that auto-paginates by calling `GET /api/v1/jobs?cursor=...` lazily. Backend pagination shape from `D4_rehydration_spec.md` §3.3 `ListJobsResponse` = `{jobs: Job[], next_cursor: string | null}`. Matches Stripe's `auto_paging_iter()` (`https://stripe.com/docs/api/pagination/auto?lang=python` accessed 2026-05-14) — same UX, simpler implementation (no separate `.auto_paging_iter()` call; iterating the result IS auto-paging).

---

## 8. Webhook signature verification helper

Bundled with SDK since Cycle AB will spec customer-facing webhook delivery (status=done, status=failed → customer endpoint).

**API** (modeled on `stripe.Webhook.construct_event` per `D4_specs.md` §4 Story #7):

```python
from agibridge import Webhook, AgibridgeSignatureError

# In your Flask/FastAPI/Django webhook handler:
try:
    event = Webhook.construct_event(
        payload=request.body,             # raw bytes, NOT parsed JSON
        sig_header=request.headers["Agibridge-Signature"],
        secret="whsec_...",               # captured from agibridge dashboard
        tolerance=300,                     # seconds; reject events older than this
    )
except AgibridgeSignatureError as e:
    return 400  # invalid signature OR replay attempt

if event.type == "job.done":
    job_id = event.data.job_id
    # ... fetch + process
```

**Signature scheme**: HMAC-SHA256 over `f"{timestamp}.{payload_bytes}"`, header value `t=<unix>,v1=<hex>`. Matches Stripe webhooks (`https://stripe.com/docs/webhooks/signatures` accessed 2026-05-14). Same shape backend Stripe webhook handler uses for inbound Stripe events (per `D4_specs.md` §4 Story #7) — keeps the verification mental model consistent across inbound and outbound webhooks.

**Tolerance default 300 s**: same as Stripe. Rejects replay attacks while tolerating clock skew.

---

## 9. Examples directory

Ship three cookbook examples in `examples/` (every one runs against a real local backend OR a documented mock — no broken pseudo-code):

### `examples/01_batch_convert.py`
Iterate a directory of AgiBot World tarballs, convert each to LeRobot v3, write outputs to `./out/`. Demonstrates: presign → upload → start → wait_until_done → download loop. Resumes on interrupt (skips jobs already in `done` state — uses `list()` filtered by filename). ~80 lines.

### `examples/02_ci_integration.py`
GitHub Actions / GitLab CI integration: convert a fixture dataset on every PR, fail the build if conversion fails, post the converted output as a CI artifact. Demonstrates: synchronous SDK in a non-Jupyter env, exit-code-driven control flow, `AgibridgeJobFailedError` handling. ~50 lines.

### `examples/03_watch_folder.py`
`watchdog`-based folder watcher: any new `.tar.gz` dropped into `./inbox/` triggers a conversion, outputs to `./outbox/`. Demonstrates: async SDK, concurrency via `asyncio.gather` with semaphore-bounded (5 concurrent jobs respects rate limit), graceful shutdown on SIGINT. ~120 lines.

Every example has a header comment block:
```python
"""
Example 02 — CI integration.
Runs against: agibridge production OR local dev backend (set $AGIBRIDGE_BASE_URL).
Requires: agibridge>=0.1, AGIBRIDGE_API_KEY env var.
Tested: 2026-05-14 against backend commit <sha>.
"""
```

Last line is enforced by CI — examples that haven't been re-tested in 30 days fail the SDK release gate. Prevents stale tutorial code drift.

---

## 10. Testing strategy

**Decision**: mock httpx via `respx` library + a single integration test suite against a local backend.

**Reasoning**:
- Unit tests against mocked `httpx` (using `respx` — `https://lundberg.github.io/respx/`, accessed 2026-05-14) cover the SDK's logic surface: retry behavior, auth header injection, error envelope parsing, pagination state machine. Fast (<5 s per suite), no network.
- Integration tests against `docker-compose up` of the agibridge backend (HF Space Dockerfile per Cycle M) cover the actual wire format. Slow (~60 s setup), runs nightly in CI, NOT on every PR.

**Don't** use VCR.py cassettes — they capture real prod responses, drift silently when backend changes, and create a "the test passes but prod is broken" failure mode. `respx` makes the contract explicit in code; backend contract changes break tests at edit time, not runtime.

**Coverage target**: 90% line, 100% branch on the public API. Internal `_transport.py` carved out — its branches exist solely for retry edge cases.

**`pytest` + `pytest-httpx` (sync) and `pytest-asyncio` (async)** are the test deps. Three suites: `tests/sync/`, `tests/async_/`, `tests/integration/` (skipped unless `AGIBRIDGE_INTEGRATION=1`).

---

## 11. Distribution

**PyPI**: publish via `uv publish` + Trusted Publisher (OIDC, no API tokens in CI) per `https://docs.pypi.org/trusted-publishers/`, accessed 2026-05-14. GitHub Actions workflow at `.github/workflows/release.yml` triggers on `v*` tag push.

**Versioning**: SemVer. `0.x` while API key model is contract-only (until D5+ ships issuance backend). `1.0.0` cuts when:
1. API key backend live in production
2. All 5 MVP endpoints stable for 30 days with no breaking change
3. First paying customer using SDK in production

**Tagging**: `git tag v0.1.0 && git push --tags`. Tag triggers PyPI publish + GitHub Release with auto-generated changelog (use `git-cliff`).

**Pre-releases**: `0.1.0a1` (alpha) → `0.1.0b1` (beta) → `0.1.0rc1` (release candidate) → `0.1.0`. Pattern from Stripe Python release history (`https://github.com/stripe/stripe-python/releases`, accessed 2026-05-14).

**SDK + backend version compatibility table** in README:

| SDK version | Backend API version | Notes |
|---|---|---|
| 0.1.x | v1 | MVP — 5 endpoints |
| 0.2.x | v1 | + webhooks (Cycle AB) |
| 1.0.x | v1 | first paying customer using SDK in production |

---

## 12. README structure (DX surface)

The README is the first thing a developer reads. Apply Cycle X voice anchor: lead with format names, terse, no marketing exclamation. Five sections:

1. **What this is** — 2 sentences. "Python SDK for agibridge. Convert AgiBot World ↔ LeRobot v3 datasets programmatically without the browser UI."
2. **Install + first call** — `pip install agibridge` + a 10-line code sample that runs. Stop reading here if you just need to ship.
3. **Concepts** — 3 paragraphs: presigned uploads (browser-direct to R2, bytes never traverse our API), job status machine (presigned → queued → running → done|failed|expired), org scoping (every API key bound to one org).
4. **Reference** — table of methods with one-line descriptions, link to full Sphinx-built docs.
5. **Cookbook** — links to the three `examples/` scripts.

Voice example: not "Get started with agibridge in seconds!" but "Install: `pip install agibridge`. Set `$AGIBRIDGE_API_KEY`. The 10 lines below convert one dataset end-to-end."

---

## 13. Open [DECISION NEEDED]

**None blocking spec authoring**. The two surface-level choices below are spec-author defaults; flag if Allen disagrees:

1. **Sync + async vs sync-only at v0.1**: spec defaults to BOTH at launch (matches Stripe). Alternative is sync-only v0.1 with async added v0.2 — saves ~3 days implementation. Recommend BOTH because CI/orchestrator users (a real wedge of the target audience) won't adopt a sync-only client.

2. **Pydantic v2 vs `attrs` vs `dataclasses` for response types**: spec defaults to Pydantic v2 (codegen-friendly, matches backend stack). `attrs` is lighter-weight but adds a non-stdlib dep without payoff. `dataclasses` (stdlib) lacks validation. Recommend Pydantic v2.

---

## 14. Sources

- `/Users/allenwu/.plans/agibridge-2026/CLAUDE.md` (charter)
- `/Users/allenwu/.plans/agibridge-2026/DECISIONS.md` DR-005 Clerk, DR-007 R2, DR-008 Stripe, DR-009 LLM-default-Anthropic
- `/Users/allenwu/embodied-data-hosted/agibridge-saas-bootstrap/dispatches/D4_specs.md` §3 cross-brief amendments
- `/Users/allenwu/embodied-data-hosted/agibridge-saas-bootstrap/frontend/D4_rehydration_spec.md` §3 endpoint inventory
- `/Users/allenwu/embodied-data-hosted/agibridge-saas-bootstrap/marketing/site-copy/homepage.md` Cycle X voice anchor
- https://pypi.org/pypi/agibridge/json — HTTP 404 (available), accessed 2026-05-14
- https://pypi.org/pypi/agibridge-sdk/json — HTTP 404 (available), accessed 2026-05-14
- https://pypi.org/pypi/embodied-data/json — HTTP 200 (Allen-owned, v0.3.1 GA), accessed 2026-05-14
- https://stripe.com/docs/api?lang=python — Stripe Python SDK reference, accessed 2026-05-14 (latest version 15.1.0 via `pypi.org/pypi/stripe/json`)
- https://stripe.com/docs/keys — Stripe key prefix convention, accessed 2026-05-14
- https://stripe.com/docs/security#secret-scanning — GitHub Secret Scanning Partner Program, accessed 2026-05-14
- https://stripe.com/docs/keys#rolling-keys — Stripe key rotation pattern, accessed 2026-05-14
- https://stripe.com/docs/api/errors?lang=python — Stripe Python error hierarchy, accessed 2026-05-14
- https://stripe.com/docs/api/usage/configuration?lang=python — Stripe per-call config override, accessed 2026-05-14
- https://stripe.com/docs/api/pagination/auto?lang=python — Stripe `auto_paging_iter`, accessed 2026-05-14
- https://stripe.com/docs/webhooks/signatures — Stripe webhook HMAC signature scheme, accessed 2026-05-14
- https://github.com/stripe/stripe-python/releases — Stripe Python release history (pre-release tag conventions), accessed 2026-05-14
- https://clerk.com/docs/references/python/overview — Clerk Python SDK (`clerk-backend-api` 5.0.6), accessed 2026-05-14
- https://clerk.com/docs/references/backend/overview — Clerk Backend Overview, accessed 2026-05-14
- https://www.python-httpx.org/ — httpx 0.28.1, accessed 2026-05-14
- https://pypi.org/project/datamodel-code-generator/ — Pydantic v2 codegen tool, accessed 2026-05-14
- https://pypi.org/project/openapi-python-client/ — alternative codegen (rejected), v0.28.4, accessed 2026-05-14
- https://lundberg.github.io/respx/ — httpx mocking library for tests, accessed 2026-05-14
- https://docs.pypi.org/trusted-publishers/ — Trusted Publisher / OIDC pattern, accessed 2026-05-14

---

**Word count** (excluding meta-header, sources, code samples): ~2,150 words — within 1,500–2,500 target.
