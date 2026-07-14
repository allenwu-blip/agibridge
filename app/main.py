"""FastAPI app entrypoint for agibridge — commercial B2B SaaS (DR-001).

agibridge converts AgiBot World and LeRobot v3 robotics datasets at scale
for paying teams. Auth is Clerk JWT (DR-005); jobs + metadata persist in
Postgres (DR-006) via the org-scoped `JobStore`; dataset bytes live in
Cloudflare R2 (DR-007) under per-org key prefixes.

D4-A changes (feat/d4a-clerk-jobstore):
- Clerk JWT middleware mounted (Story #2). Exempts health/version + the
  defensively-reserved Stripe webhook path.
- F-1 ephemeral wording removed (Refactor #1, DR-001).
- CORS `*` → allowlist (Refactor #2, DR-016 domain `agibridge.dev`).
- upload/status/download → DB-backed org-scoped `jobs` router
  (Refactor #3). The verified-working `SubprocessRunner` conversion core
  (DR-019) is preserved unchanged; only the auth + storage layer around
  it changed.
- In-memory `SessionStore` + `PurgeReaper` wired down (Refactor #4):
  no longer the source of truth, no longer in the lifespan.

D4-B adds Stripe (`app/api/billing.py`): Checkout + Customer Portal
(Clerk-JWT-gated) + the subscription-lifecycle webhook (Stripe-Signature
HMAC only, exempt from Clerk middleware at `clerk_auth.py:62`) + Story #8
tier gating wired into `POST /api/v1/jobs` (`app/api/tier_gating.py`).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from slowapi.errors import RateLimitExceeded

from app.api import billing, demo, events, health, jobs, version, webhooks
from app.api.clerk_auth import ClerkJWTMiddleware
from app.api.rate_limit import limiter, rate_limit_handler
from app.api.subprocess_runner import SubprocessRunner

# The verified-working subprocess runner (DR-019). Single in-process
# instance — single uvicorn worker, single-flight (spec §3). Unchanged.
_runner = SubprocessRunner()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: emit a one-shot embodied-data version-drift warning.
    Shutdown: SIGKILL any live conversion subprocess group (spec §4 step 5
    / §7 HP-2). No SessionStore / PurgeReaper anymore (Refactor #4) — job
    lifecycle is the DB's responsibility; R2 object retention is DevOps's
    (per-tier windows, DR-016 / deferred)."""
    await health.log_version_drift_once()
    try:
        yield
    finally:
        await _runner.shutdown()


app = FastAPI(
    title="agibridge",
    version="0.0.1",
    description=(
        "Convert AgiBot World and LeRobot v3 robotics datasets at scale. "
        "Authenticated, org-scoped, with durable storage. "
        "Built on the open-source embodied-data toolkit."
    ),
    lifespan=lifespan,
)

# D4-harden Check 3a: per-IP rate limiting on the PUBLIC pre-auth endpoints
# (/health, /version, /billing/webhook). The @limiter.limit decorators live
# on the individual route handlers (app/api/health.py, version.py,
# billing.py); slowapi requires the limiter on app.state + its exception
# handler registered. House-envelope 429 via rate_limit_handler. Minimal
# slice of specs/rate-limit-abuse-v1.md — Clerk-gated routes are out of
# scope (Clerk already gates; per-tier limits are deferred D5 Cycle AU).
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, rate_limit_handler)


# CORS allowlist (Refactor #2). Auth is now a real boundary (Clerk JWT),
# so CORS is too — no wildcard. Origins: the live HF Space, the commercial
# domain (DR-016 = agibridge.dev), and Vercel preview/prod. Vercel preview
# URLs are per-deploy hashes under *.vercel.app; we use a regex for those
# plus the wildcard apex domain.
_ALLOW_ORIGINS = [
    "https://allenwu06-agibridge.hf.space",
    "https://agibridge.dev",
    "https://www.agibridge.dev",
    "http://localhost:5173",  # local Vite dev (frontend-dev iteration)
    "http://localhost:3000",
]
# Vercel preview deploys + any agibridge.dev subdomain.
_ALLOW_ORIGIN_REGEX = r"https://([a-z0-9-]+\.)*(agibridge\.dev|vercel\.app)"

app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOW_ORIGINS,
    allow_origin_regex=_ALLOW_ORIGIN_REGEX,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

# Clerk JWT middleware (Story #2). Added AFTER CORS so CORS runs outermost
# (preflight + headers apply even to 401 responses — Starlette runs
# middleware in reverse add order; last-added is outermost-but-one. CORS
# added first → outermost). The middleware reads CLERK_ISSUER_URL +
# CLERK_JWKS_URL from env at first protected request; tests inject a
# fake jwks_client + issuer instead.
app.add_middleware(ClerkJWTMiddleware)


# Wire the verified runner onto the jobs router. health needs no store
# anymore (active_session is a legacy SessionStore concept); we pass None
# so /health still answers liveness without the in-memory registry.
jobs.set_state(_runner)
# The no-signup demo (`app/api/demo.py`) reuses the SAME verified runner
# instance — single-flight is the box-wide invariant, so the demo and the
# authed jobs path share one process-global runner (the demo additionally
# guards with its own non-blocking lock; see app/api/demo.py).
demo.set_state(_runner)
health.set_state(None)


app.include_router(jobs.router, prefix="/api/v1")
app.include_router(health.router, prefix="/api/v1")
app.include_router(version.router, prefix="/api/v1")
# Stripe billing (D4-B). /billing/checkout + /billing/portal are
# Clerk-JWT-gated by the middleware above; /billing/webhook is exempt at
# `app/api/clerk_auth.py:62` (verified pre-existing from D4-A, NOT re-added
# here — it authenticates via Stripe-Signature HMAC only,
# stripe_webhook_spec.md:240).
app.include_router(billing.router, prefix="/api/v1")
# Clerk auto-personal-org provisioning. /webhooks/clerk is Clerk-exempt at
# `app/api/clerk_auth.py:62` (added there with the Stripe webhook, NOT
# re-added here) — it authenticates via the Svix signature triple only and
# is the path that bootstraps a user's org before any agibridge JWT exists.
app.include_router(webhooks.router, prefix="/api/v1")
# Full-funnel instrumentation. /events is Clerk-exempt at
# `app/api/clerk_auth.py:62` (added there alongside the webhooks, NOT
# re-added here) — it ingests client-reported top-of-funnel events that
# fire before any agibridge JWT exists. Untrusted: strict body validation +
# per-IP rate limit; writes only the anonymous non-PII `funnel_events`
# table. Server-trustworthy conversions are emitted inside the Clerk/Stripe
# webhook handlers (`app/api/webhooks.py`, `app/api/billing.py`).
app.include_router(events.router, prefix="/api/v1")
# Public "try it, no signup" demo. /demo/convert is Clerk-exempt at
# `app/api/clerk_auth.py` (added alongside /events, NOT re-added here) — it
# runs the bundled AgiBot sample through the UNCHANGED verified converter
# before any agibridge JWT exists. Untrusted: takes no body/upload (fixed
# server-side input), per-IP rate-limited, process-global single-flight,
# writes only the anonymous non-PII `funnel_events` table; never touches
# org/jobs/usage_events/per-org R2 (R3 untouched).
app.include_router(demo.router, prefix="/api/v1")


# Static SPA. The frontend agent will populate app/static/ with the bundle.
_STATIC_DIR = Path(__file__).parent / "static"
if _STATIC_DIR.exists():
    app.mount(
        "/assets", StaticFiles(directory=_STATIC_DIR / "assets", check_dir=False), name="assets"
    )


@app.get("/", include_in_schema=False, response_model=None)
async def root() -> JSONResponse | FileResponse:
    """Serve index.html if present; otherwise a minimal JSON placeholder.
    Public (exempt from Clerk middleware) — it's the marketing shell."""
    index = _STATIC_DIR / "index.html"
    if index.is_file():
        return FileResponse(index, media_type="text/html")
    return JSONResponse(
        {
            "ok": True,
            "note": "Frontend bundle not yet shipped. See /docs for the API.",
        }
    )
