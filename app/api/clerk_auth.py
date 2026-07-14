"""Clerk JWT verification middleware.

D4-A Story #2 (`dispatches/D4_specs.md` §4). Verifies the
`Authorization: Bearer <Clerk JWT>` header on every request EXCEPT the
exemption list, extracts the Clerk user id (`sub`) and the active
organization id, and injects them onto `request.state` for downstream
handlers + the DB-backed `JobStore` (R3 org-scoping).

Why a custom Starlette middleware instead of a FastAPI dependency:
the refactored routes (`upload.py` / `status.py` / `download.py`) all need
`request.state.org_id` and the webhook path needs a true bypass *before*
routing; a single middleware is the one place the exemption list lives
(`app/main.py` mount + `stripe_webhook_spec.md:240`).

Clerk claim contract (source-grounded, accessed 2026-05-16):
- `sub` = Clerk user id (`user_xxx`). https://clerk.com/docs/backend-requests/resources/session-tokens
- Active org id: Clerk **v2** session tokens nest it as `o.id`; **v1**
  (deprecated) + custom JWT templates use a flat `org_id`. We accept
  BOTH so a frontend on either token version works.
  https://clerk.com/docs/backend-requests/resources/session-tokens (2026-05-16)
- `iss` = `https://<frontend-api>.clerk.accounts.dev` (dev instance) or
  `https://clerk.<domain>` (prod). We pin it via `CLERK_ISSUER_URL`.
- Signature: RS256, public keys at the instance JWKS endpoint
  `<frontend-api>/.well-known/jwks.json`.
  https://clerk.com/docs/backend-requests/handling/manual-jwt (2026-05-16)

Status taxonomy (pinned by `tests/test_clerk_jwt.py`):
- 401 = "we don't know who you are": missing/malformed header, bad
  signature, expired, wrong issuer.
- 403 = "we know who you are but you can't do this": valid JWT, no org
  claim (user has not joined/created a Clerk Organization). The
  middleware does NOT synthesize a `personal:<user_id>` org — the stub
  `test_missing_org_id_claim_returns_403_authn_ok_authz_fail` and
  `app/db/models.py:141-148` make 403 the source-grounded contract.

Anthropic-over-OpenAI: no LLM here; pure RS256 crypto.
"""

from __future__ import annotations

import os
from typing import Any

import jwt
from jwt import PyJWKClient
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp

# Paths that MUST NOT require a Clerk JWT.
#   - /api/v1/health, /api/v1/version: liveness/transparency (D1-D3 public).
#   - /api/v1/billing/webhook: authenticates via Stripe-Signature HMAC, NOT
#     Clerk. Exempted defensively even though billing.py is not built this
#     round (D4_specs.md §3.1 + stripe_webhook_spec.md:240).
#   - /api/v1/webhooks/clerk: authenticates via the Svix signature triple
#     (svix-id/svix-timestamp/svix-signature), NOT a Clerk JWT — it is the
#     path that BOOTSTRAPS a user's personal org, so by construction the
#     caller (Clerk's Svix delivery) has no agibridge JWT. Same posture as
#     the Stripe webhook line above (`app/api/webhooks.py`).
#   - /api/v1/events: client-reported top-of-funnel instrumentation
#     (`app/api/events.py`). Same structural reason as the webhooks: the
#     events it carries (`landing_view`/`pricing_view`/`signup_started`/...)
#     are emitted BEFORE the user has an org-bearing JWT, so requiring the
#     Clerk JWT here would make the whole top of the funnel unmeasurable.
#     Unlike the webhooks it has no signature; it is hardened instead by
#     strict body validation + a per-IP rate limit and writes ONLY the
#     anonymous, non-PII `funnel_events` table — it never reads/derives an
#     org, so R3 is untouched (`app/api/events.py` module docstring).
#   - /api/v1/demo/convert: the public "try it, no signup" demo
#     (`app/api/demo.py`). SAME structural reason as /api/v1/events: the
#     whole point is that it runs BEFORE the visitor has an account, so an
#     org-bearing JWT cannot exist yet — requiring one would defeat the
#     friction-removal it's built for. It is hardened with the same posture
#     as /events (no signature, so: takes NO body/upload — the input is a
#     fixed server-side sample; per-IP rate limit + a process-global
#     single-flight lock; writes ONLY the anonymous, non-PII funnel_events
#     table). It NEVER reads/derives an org and never writes
#     usage_events/jobs/per-org R2, so R3 is untouched
#     (`app/api/demo.py` module docstring).
#   - /, /docs, /openapi.json, /redoc, /assets: public shell + API docs.
_EXEMPT_EXACT = frozenset(
    {
        "/api/v1/health",
        "/api/v1/version",
        "/api/v1/billing/webhook",
        "/api/v1/webhooks/clerk",
        "/api/v1/events",
        "/api/v1/demo/convert",
        "/",
        "/docs",
        "/openapi.json",
        "/redoc",
    }
)
_EXEMPT_PREFIXES = ("/assets/",)


def _is_exempt(path: str) -> bool:
    if path in _EXEMPT_EXACT:
        return True
    return any(path.startswith(p) for p in _EXEMPT_PREFIXES)


class _AuthError(Exception):
    """Internal: (status_code, code, message) for a uniform JSON 401/403."""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        super().__init__(message)


def _parse_bearer(header_value: str | None) -> str:
    """Extract the raw token from an `Authorization: Bearer <token>` header.

    Rejects (401) on: absent header, non-Bearer scheme, empty token, or a
    token containing whitespace (the `Bearer  abc` double-space copy-paste
    bug — split() collapses it but a real token never has internal spaces).
    """
    if not header_value:
        raise _AuthError(401, "missing_authorization", "Authorization header required.")
    parts = header_value.split(" ")
    # Reject anything that isn't exactly `Bearer <token>` with no extra/empty
    # segments. `Bearer` (no token) → len 1. `Bearer  abc` → ['Bearer','','abc'].
    if len(parts) != 2 or parts[0] != "Bearer":
        raise _AuthError(401, "invalid_authorization", "Malformed Authorization header.")
    token = parts[1]
    if not token:
        raise _AuthError(401, "invalid_authorization", "Empty bearer token.")
    return token


def _extract_org_id(claims: dict[str, Any]) -> str | None:
    """Read the active org id from Clerk claims.

    Order: v2 nested `o.id` → flat `org_id` (v1 / custom JWT template).
    Returns None if the user has no active organization.
    """
    o = claims.get("o")
    if isinstance(o, dict):
        oid = o.get("id")
        if isinstance(oid, str) and oid:
            return oid
    flat = claims.get("org_id")
    if isinstance(flat, str) and flat:
        return flat
    return None


class ClerkJWTMiddleware(BaseHTTPMiddleware):
    """Starlette middleware: verify Clerk JWT, inject `request.state.user_id`
    + `request.state.org_id`. 401/403 short-circuit before the route runs.

    The JWKS client is constructed lazily on first protected request so test
    collection / health checks never touch the network, and so a test can
    inject a fake `jwks_client` via the `jwks_client` ctor arg.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        jwks_client: PyJWKClient | None = None,
        issuer: str | None = None,
        jwks_url: str | None = None,
    ) -> None:
        super().__init__(app)
        self._jwks_client = jwks_client
        # Allow explicit overrides (tests); else read env at first use.
        self._issuer = issuer
        self._jwks_url = jwks_url

    def _get_jwks_client(self) -> PyJWKClient:
        if self._jwks_client is None:
            url = self._jwks_url or os.environ.get("CLERK_JWKS_URL")
            if not url:
                raise _AuthError(
                    401,
                    "auth_misconfigured",
                    "JWT verification is not configured on the server.",
                )
            # PyJWKClient caches keys in-process with its own lifespan; one
            # per middleware instance is the documented pattern.
            # https://pyjwt.readthedocs.io/en/stable/usage.html#retrieve-rsa-signing-keys-from-a-jwks-endpoint
            self._jwks_client = PyJWKClient(url, cache_keys=True)
        return self._jwks_client

    def _expected_issuer(self) -> str | None:
        return self._issuer if self._issuer is not None else os.environ.get("CLERK_ISSUER_URL")

    def _verify(self, token: str) -> dict[str, Any]:
        """Verify signature + standard claims. Raises _AuthError on any
        failure. Returns the decoded claims dict on success."""
        jwks_client = self._get_jwks_client()
        try:
            signing_key = jwks_client.get_signing_key_from_jwt(token)
        except Exception as exc:  # PyJWKClientError / decode of header failed
            raise _AuthError(401, "invalid_token", "Could not resolve signing key.") from exc

        expected_iss = self._expected_issuer()
        decode_kwargs: dict[str, Any] = {
            "algorithms": ["RS256"],
            "options": {"require": ["exp", "iss", "sub"]},
        }
        if expected_iss:
            decode_kwargs["issuer"] = expected_iss
        try:
            claims = jwt.decode(token, signing_key.key, **decode_kwargs)
        except jwt.ExpiredSignatureError as exc:
            raise _AuthError(401, "token_expired", "Session token has expired.") from exc
        except jwt.InvalidIssuerError as exc:
            raise _AuthError(401, "invalid_issuer", "Token issuer is not trusted.") from exc
        except jwt.InvalidTokenError as exc:
            # Bad signature, missing required claim, malformed, etc.
            raise _AuthError(401, "invalid_token", "Invalid session token.") from exc
        return claims

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        if request.method == "OPTIONS" or _is_exempt(request.url.path):
            return await call_next(request)

        try:
            token = _parse_bearer(request.headers.get("Authorization"))
            claims = self._verify(token)
            user_id = claims.get("sub")
            if not isinstance(user_id, str) or not user_id:
                raise _AuthError(401, "invalid_token", "Token missing subject.")
            org_id = _extract_org_id(claims)
            if org_id is None:
                # authn OK, authz fail — distinct from 401 so the frontend can
                # show a "create/join an organization" CTA instead of logging
                # the user out. Source: test_clerk_jwt.py:140-158 +
                # app/db/models.py:141-148.
                raise _AuthError(
                    403,
                    "no_organization",
                    "Your account is not part of an organization yet.",
                )
        except _AuthError as err:
            return JSONResponse(
                status_code=err.status_code,
                content={"code": err.code, "message": err.message, "suggestion": None},
            )

        request.state.user_id = user_id
        request.state.org_id = org_id
        return await call_next(request)
