"""Client-reported funnel events — `POST /api/v1/events`.

Full-funnel instrumentation (brief: make first-customer behavior
measurable). This endpoint accepts ONLY the top-of-funnel events the
browser can legitimately report:

    landing_view  signup_started  dashboard_view  pricing_view
    checkout_started

The server-trustworthy CONVERSIONS are deliberately NOT acceptable here —
they are emitted server-side inside the existing webhook transactions so
the client cannot forge a conversion:
  - `signup_completed`  ← Clerk `user.created` webhook (`app/api/webhooks.py`)
  - `checkout_completed` ← Stripe webhook (`app/api/billing.py`)
That is the client-vs-server trust boundary: a browser may report INTENT
(viewed/started); only Stripe/Clerk (HMAC-verified callers) may assert a
COMPLETION.

Auth posture — Clerk-EXEMPT:
  This endpoint is added to `app/api/clerk_auth.py:_EXEMPT_EXACT` for the
  SAME structural reason the webhooks are exempt: the events it carries are
  emitted BEFORE a user has an org-bearing JWT (`landing_view` /
  `pricing_view` fire on the public marketing page; `signup_started` fires
  on the sign-up CTA, pre-account). Requiring the Clerk JWT here would make
  the entire top of the funnel unmeasurable. Unlike the webhooks it has NO
  signature to verify — so it is treated as fully untrusted input: strict
  body validation, a per-IP rate limit (reusing `app/api/rate_limit.py`),
  and it writes ONLY to the anonymous, non-PII `funnel_events` table
  (`app/db/models.py:FunnelEvent`). It never reads/derives an org and never
  writes `usage_events`, so R3 org-scoping is untouched.

Privacy:
  The only correlator stored is the client-generated random `anon_id`
  (a localStorage UUID — NOT a fingerprint, NOT an IP, NOT an email).
  `meta` is a tiny allow-shaped JSON map (scalar values only, bounded
  size). No PII enters `funnel_events`.

Never break the user flow:
  Instrumentation is best-effort. A malformed body is a clean 400 (NOT a
  500 traceback leak); a well-formed body is a 204 No Content. The frontend
  `track()` helper is fire-and-forget and swallows everything anyway, so
  even the 400/429 never reaches the UI — but we still return precise
  statuses for observability and to keep the contract honest.

Anthropic-over-OpenAI: no LLM here; pure validation + one INSERT.
"""

from __future__ import annotations

import logging
from enum import Enum

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field, ValidationError, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.rate_limit import limiter
from app.db.models import FunnelEvent
from app.db.session import get_session

logger = logging.getLogger("agibridge.events")

router = APIRouter()

# Per-IP limit for the public, signature-less events endpoint. The browser
# emits at most a handful of funnel events per page; 120/min/IP absorbs a
# normal session (multiple tabs, fast navigation) while still capping a
# forge/flood. Same in-memory single-worker slowapi posture + reasoning as
# `WEBHOOK_LIMIT` (`app/api/rate_limit.py:54-58`); kept local so the events
# scope does not perturb the webhook/health numbers.
EVENTS_LIMIT = "120/minute"

# Bound `meta` so a forged client can't write a huge JSON blob into the
# append-only table (the table has no FK to gate growth; this is the gate).
_MAX_META_KEYS = 12
_MAX_META_STR = 256
_MAX_ANON_ID = 64


class ClientEventKind(str, Enum):
    """Allowlist — ONLY browser-reportable INTENT events. The conversions
    (`signup_completed`, `checkout_completed`) are intentionally absent so a
    client cannot self-report a conversion; those are server-emitted into
    `usage_events` by the Clerk/Stripe webhooks (see module docstring)."""

    landing_view = "landing_view"
    signup_started = "signup_started"
    dashboard_view = "dashboard_view"
    pricing_view = "pricing_view"
    checkout_started = "checkout_started"
    # The visitor clicked "Try it, no signup" on Landing. Browser-reportable
    # INTENT (fires pre-account, same trust class as landing_view); the
    # server-trustworthy `demo_run` (the conversion actually ran) is emitted
    # server-side by `app/api/demo.py`, NOT here — same client-INTENT vs
    # server-COMPLETION boundary as signup_started vs signup_completed.
    demo_started = "demo_started"
    # A signed-in user submitted the Dashboard upload form — the FIRST act
    # of converting their own data (Track 3: signup→first-conversion funnel).
    # Browser-reportable INTENT only: it fires when the upload chain STARTS,
    # before any bytes land. The conversion actually running is the existing
    # server-side `job` lifecycle in `usage_events` (`app/api/jobs.py`) — same
    # client-INTENT vs server-truth boundary as signup_started vs the
    # webhook-emitted signup_completed. No new table, no migration: it is one
    # more free-text `funnel_events.kind` string (`app/db/models.py:326`).
    conversion_started = "conversion_started"


class EventIn(BaseModel):
    """Untrusted request body. Pydantic does the allowlist + shape enforcement
    so an out-of-allowlist `kind`, an oversized `anon_id`, or a non-scalar /
    oversized `meta` is a clean 422→(normalized 400), never a 500."""

    model_config = {"extra": "forbid"}

    kind: ClientEventKind
    anon_id: str = Field(min_length=1, max_length=_MAX_ANON_ID)
    meta: dict[str, str | int | float | bool | None] = Field(default_factory=dict)

    @field_validator("anon_id")
    @classmethod
    def _anon_id_no_whitespace(cls, v: str) -> str:
        # The client sends a uuid4; reject anything with whitespace/control
        # chars so a junk value can't masquerade as an id.
        if v != v.strip() or any(c.isspace() for c in v):
            raise ValueError("anon_id must not contain whitespace")
        return v

    @field_validator("meta")
    @classmethod
    def _meta_small(
        cls, v: dict[str, str | int | float | bool | None]
    ) -> dict[str, str | int | float | bool | None]:
        if len(v) > _MAX_META_KEYS:
            raise ValueError(f"meta has too many keys (max {_MAX_META_KEYS})")
        for key, val in v.items():
            if len(key) > _MAX_META_STR:
                raise ValueError("meta key too long")
            if isinstance(val, str) and len(val) > _MAX_META_STR:
                raise ValueError("meta value too long")
        return v


def _bad_request(message: str) -> JSONResponse:
    """House error envelope shape (`app/api/billing.py:_err` /
    `app/api/jobs.py:_err`: `{detail:{code,message,suggestion}}`). A bad
    event body must never 500 — instrumentation must not break the flow."""
    return JSONResponse(
        status_code=400,
        content={
            "detail": {
                "code": "invalid_event",
                "message": message,
                "suggestion": None,
            }
        },
    )


# response_model=None: the handler returns a `Response` (204) OR a
# `JSONResponse` (400) union; FastAPI must NOT try to derive a Pydantic
# response model from that annotation (same posture as the union-returning
# root route, `app/main.py:147`).
@router.post("/events", include_in_schema=True, response_model=None)
@limiter.limit(EVENTS_LIMIT)
async def ingest_event(
    request: Request,
    db: AsyncSession = Depends(get_session),
) -> Response | JSONResponse:
    """Record one anonymous top-of-funnel event.

    Flow (mirrors the webhook discipline of NEVER leaking an internal error
    on a public unauthenticated surface, `app/api/billing.py:384-451`):
      1. parse JSON — malformed JSON → clean 400 (NOT 500).
      2. validate against the allowlist model → 400 on any violation.
      3. INSERT one `funnel_events` row (anonymous, non-PII) → 204.
    """
    # Step 1 — body bytes → JSON. A non-JSON / empty body is client error,
    # not a server fault: clean 400, no traceback.
    try:
        payload = await request.json()
    except Exception:
        # Any decode failure (bad JSON, empty body, wrong content) is the
        # same client error — a clean 400, never a 500 traceback leak.
        return _bad_request("Request body must be valid JSON.")

    if not isinstance(payload, dict):
        return _bad_request("Request body must be a JSON object.")

    # Step 2 — strict allowlist validation. ValidationError → 400 (Pydantic
    # would otherwise surface as 422; we normalize to the house 400 so the
    # contract is "bad event → 400, never 500").
    try:
        event = EventIn.model_validate(payload)
    except ValidationError:
        return _bad_request("Event failed validation (kind/anon_id/meta).")

    # Step 3 — single INSERT. get_session() commits on clean return / rolls
    # back on raise (`app/db/session.py:52-63`). No org is read or written;
    # `funnel_events` has no FK so this never touches R3-scoped state.
    db.add(
        FunnelEvent(
            anon_id=event.anon_id,
            kind=event.kind.value,
            meta=event.meta,
        )
    )
    # 204 No Content — the client `track()` helper is fire-and-forget and
    # ignores the body anyway; an empty success is the smallest honest reply.
    return Response(status_code=204)
