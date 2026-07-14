"""D4-harden Check 3a — application-layer rate limiting for the PUBLIC
pre-auth endpoints, ahead of the HF Space public flip.

Scope (minimal, NOT the full Cycle AU spec): only the three Clerk-exempt
public endpoints — `/api/v1/health`, `/api/v1/version`,
`/api/v1/billing/webhook`. The per-org token-bucket / Postgres daily-counter
machinery in `specs/rate-limit-abuse-v1.md` §2 is the AUTHORITATIVE D5+
contract and is intentionally NOT implemented here; Clerk-gated endpoints
(jobs / billing-checkout / portal) are already auth-gated and their
per-tier limits are the deferred Cycle AU D5 work (spec §1, §5 DR-024).

Design references (source-grounded):
- `specs/rate-limit-abuse-v1.md:104-132` — 429 envelope: RFC 6585 + Stripe
  convention, `Retry-After` + `X-RateLimit-*`, `{detail:{code,message,
  suggestion}}` body shape (matches the house envelope at
  `app/api/billing.py:_err` / `app/api/jobs.py:127-131`).
- `specs/rate-limit-abuse-v1.md:42` — unauthenticated path keys on the
  first-hop IP; HF Space sits behind Cloudflare so `X-Forwarded-For` first
  hop is the client. slowapi's `get_remote_address` reads the connecting
  peer; we wrap it to prefer the first `X-Forwarded-For` hop when present
  (HF Space sets it), falling back to the socket peer.

Storage = in-memory (slowapi default). Justified for THIS slice:
single uvicorn worker on a single HF Space (`app/main.py:41-42`), no Redis
(DR — `specs/rate-limit-abuse-v1.md:100`). A container restart resets the
window; for a per-IP *burst* gate that is acceptable (spec §3.1 makes the
same call for the failed-auth deny list). The Postgres-backed *daily*
counters that MUST survive restart are explicitly the D5+ per-org work.

slowapi API verified empirically against slowapi==0.1.9 / limits==5.8.0:
- `Limiter(key_func, headers_enabled=True)` injects `X-RateLimit-Limit`,
  `X-RateLimit-Remaining`, `X-RateLimit-Reset`, `Retry-After`.
- `RateLimitExceeded` subclasses `starlette.exceptions.HTTPException`.
- the decorated endpoint MUST take a `request: Request` arg or slowapi
  cannot hook the limit (slowapi docs, accessed 2026-05-17:
  https://slowapi.readthedocs.io/en/latest/).
"""

from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

# Conservative per-IP limits (brief + spec §1 "Unauthenticated (demo)" row
# is stricter still at 2/60s, but that row is the demo subprocess endpoint;
# /health + /version are cheap liveness probes and Stripe legitimately
# bursts webhook retries, so we use the brief's looser, endpoint-shaped
# numbers here):
#   /health, /version       -> 60/min/IP  (cheap probes; allows a monitor +
#                               a human curling without 429 noise)
#   /billing/webhook         -> 120/min/IP (Stripe retry storms are
#                               legitimate; spec §2.3 "retry storm" is a
#                               first-class scenario — must not 429 Stripe)
HEALTH_VERSION_LIMIT = "60/minute"
WEBHOOK_LIMIT = "120/minute"


def _client_key(request: Request) -> str:
    """Key on the real client IP. HF Space terminates behind Cloudflare and
    sets `X-Forwarded-For`; the FIRST hop is the originating client (spec
    §2 amendment / `specs/rate-limit-abuse-v1.md:42`). Never trust a
    request-body field for the key (R3 forge-vector discipline, spec
    §1 "Authentication-path resolution"). Falls back to slowapi's socket
    peer when no XFF (local/dev/direct)."""
    xff = request.headers.get("x-forwarded-for")
    if xff:
        first = xff.split(",")[0].strip()
        if first:
            return first
    return get_remote_address(request)


# headers_enabled=True -> Retry-After + X-RateLimit-* on the 429
# (verified: Limiter._inject_headers, slowapi==0.1.9). Default storage is
# in-memory ("memory://") which is what we want for this single-worker slice.
limiter = Limiter(key_func=_client_key, headers_enabled=True)


def rate_limit_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    """House-envelope 429 (NOT slowapi's default `{"error": ...}` body) so
    the public rate-limit response matches every other 4xx in this API
    (`{detail:{code,message,suggestion}}` per
    `specs/rate-limit-abuse-v1.md:115-123` + `billing.py:_err`). We still
    delegate to slowapi's `_inject_headers` so `Retry-After` /
    `X-RateLimit-*` are computed from the live window stats — re-deriving
    them by hand would drift from the limiter's own accounting."""
    response = JSONResponse(
        status_code=429,
        content={
            "detail": {
                "code": "rate_limit_exceeded",
                "message": f"Rate limit exceeded: {exc.detail}.",
                "suggestion": "Wait for the Retry-After interval, then retry.",
            }
        },
    )
    # request.state.view_rate_limit is set by slowapi's middleware on the
    # limited route; reuse the limiter's own header math (Retry-After +
    # X-RateLimit-*). Mirrors slowapi._rate_limit_exceeded_handler.
    return request.app.state.limiter._inject_headers(response, request.state.view_rate_limit)
