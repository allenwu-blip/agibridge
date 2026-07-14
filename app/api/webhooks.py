"""Clerk lifecycle webhook — auto personal-org provisioning.

Endpoint: POST /api/v1/webhooks/clerk

Clerk Organizations is DISABLED on the instance
(relevant-mosquito-93.clerk.accounts.dev) and we are NOT enabling it. Org
scoping instead rides the Clerk JWT's flat `org_id` claim, emitted by a
custom JWT template named `agibridge` that reads
`{{user.public_metadata.org_id}}` (validated live; see
`app/api/clerk_auth.py:107-121` `_extract_org_id`). For that claim to exist
every user needs `public_metadata.org_id` set. This webhook is what sets
it: on `user.created` it mints a deterministic personal org, mirrors the
`orgs`/`users` rows, then writes `public_metadata.org_id` back to Clerk.

Why a Svix-signed webhook (NOT a Clerk-JWT route): this path BOOTSTRAPS the
org, so by construction the caller has no agibridge JWT yet — it is Clerk's
Svix delivery. Exempt from the Clerk JWT middleware at
`app/api/clerk_auth.py:62` (verified added there, NOT re-added here), the
exact posture the Stripe webhook uses (`app/api/billing.py:14-16`).

This handler MIRRORS `app/api/billing.py` (the reference signature-verified
Clerk-exempt webhook): raw body, structural header pre-check → 400, real
verification failure → 403, house error envelope, idempotent side effects,
no internal-exception leak on a public unauthenticated surface.

Svix verification taxonomy (verified empirically against svix==1.93.0):
  `svix.webhooks.Webhook(secret).verify(body, headers)` returns the parsed
  JSON payload and raises `WebhookVerificationError` on bad HMAC, a stale
  timestamp (replay), OR missing/garbage `svix-*` headers — i.e. it
  COLLAPSES "malformed input" and "verification failed" into ONE exception,
  exactly like `stripe.Webhook.construct_event`
  (`app/api/billing.py:391-396`). So we keep the SAME split: a cheap
  structural pre-check on the `svix-id`/`svix-timestamp`/`svix-signature`
  triple decides 400 (missing/malformed → misconfig/forgery/probe) vs 403
  (well-formed but the crypto/timestamp check failed). The pre-check is
  intentionally looser than the real HMAC verify (which `verify` still
  performs) — its only job is the 400-vs-403 split, never to replace
  verification.
  https://docs.svix.com/receiving/verifying-payloads/how (accessed 2026-05-17)

Clerk Backend API (merge-not-clobber, accessed 2026-05-17):
  GET  https://api.clerk.com/v1/users/{id}  -> user object incl.
       `public_metadata` (Bearer CLERK_SECRET_KEY).
  PATCH https://api.clerk.com/v1/users/{id} with body
       {"public_metadata": {...}} FULL-REPLACES `public_metadata`
       (https://clerk.com/docs/reference/backend/user/update-user,
       accessed 2026-05-17). The brief pins THIS endpoint; the
       merge-not-clobber safety is achieved client-side: we GET first,
       skip entirely if `org_id` is already present (idempotent), else
       PATCH `{**existing_public_metadata, "org_id": org_id}` so existing
       keys survive the full-replace.

Anthropic-over-OpenAI: no LLM here; pure Svix HMAC + REST.
"""

from __future__ import annotations

import logging

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession
from svix.webhooks import Webhook, WebhookVerificationError

from app.api.billing_config import settings
from app.api.rate_limit import WEBHOOK_LIMIT, limiter
from app.db.models import Org, OrgTier, UsageEvent, User
from app.db.session import get_session

logger = logging.getLogger("agibridge.webhooks.clerk")

router = APIRouter()

# Clerk Backend API base. The PATCH/GET use the org-deterministic user id;
# Bearer auth is the instance secret key (orchestrator/HF secret).
_CLERK_API_BASE = "https://api.clerk.com/v1"
# Defensive timeout on the outbound Clerk call so a Clerk API hang can't
# pin a webhook worker (Clerk retries the delivery; we'd rather 5xx fast).
_CLERK_API_TIMEOUT_S = 10.0


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _is_well_formed_svix_headers(
    svix_id: str | None,
    svix_timestamp: str | None,
    svix_signature: str | None,
) -> bool:
    """Structural validity of the Svix header triple BEFORE the HMAC verify,
    so a missing/garbage header is a clean 400 (`invalid_request`) and only a
    real crypto/timestamp failure on a well-formed triple becomes a 403
    (`signature_verification_failed`). Direct analogue of
    `app/api/billing.py:96-119` `_is_well_formed_sig_header`.

    Svix ALWAYS sends all three: `svix-id` (a `msg_...`-style id),
    `svix-timestamp` (unix seconds, all-digit), and `svix-signature` (one or
    more space-separated `v1,<base64>` tokens). We require: all three
    non-empty, an all-digit timestamp, and a signature carrying at least one
    `vN,<sig>` element. Looser than the real HMAC check (which
    `Webhook.verify` still performs) — only the 400-vs-403 split.
    https://docs.svix.com/receiving/verifying-payloads/how (accessed 2026-05-17)
    """
    if not svix_id or not svix_timestamp or not svix_signature:
        return False
    if not svix_timestamp.isdigit():
        return False
    # Each space-separated token is `version,signature`; require ≥1 with a
    # non-empty version AND signature half (Svix uses `v1,<b64>`).
    return any(v and s for v, _, s in (tok.partition(",") for tok in svix_signature.split(" ")))


def _primary_email(data: dict) -> str | None:
    """Resolve the user's primary email from a Clerk `user.created` payload.

    Clerk shape (https://clerk.com/docs/webhooks/overview, accessed
    2026-05-17): `data.email_addresses` is a list of
    `{id, email_address, ...}` and `data.primary_email_address_id` points at
    one element's `id`. Prefer the primary; fall back to the first address
    so a user with exactly one (unmarked) email still provisions cleanly."""
    addresses = data.get("email_addresses")
    if not isinstance(addresses, list) or not addresses:
        return None
    primary_id = data.get("primary_email_address_id")
    if isinstance(primary_id, str):
        for addr in addresses:
            if isinstance(addr, dict) and addr.get("id") == primary_id:
                email = addr.get("email_address")
                if isinstance(email, str) and email:
                    return email
    first = addresses[0]
    if isinstance(first, dict):
        email = first.get("email_address")
        if isinstance(email, str) and email:
            return email
    return None


async def _provision_org_and_user(
    db: AsyncSession, *, user_id: str, org_id: str, email: str | None
) -> bool:
    """Idempotently mirror the personal org + user rows.

    Returns True iff the `users` row was NEWLY inserted by THIS delivery
    (i.e. a genuine first-time sign-up), False if a duplicate Clerk delivery
    found it already present. This boolean is the idempotency anchor for the
    `signup_completed` funnel event: emit it exactly once per user, on the
    delivery that actually created the user — same "ON CONFLICT DO NOTHING
    rowcount tells you if it was new" discipline as the org/user inserts
    themselves, NOT a new event-id table.

    INSERT ... ON CONFLICT DO NOTHING on BOTH PKs (`orgs.id`, `users.id`) so a
    duplicate Clerk delivery (Svix retries, dashboard resend) is a true
    no-op — same idempotency posture as the Stripe `stripe_events.id` PK
    short-circuit (`app/api/billing.py:456-473`), but expressed as the
    INSERT-OR-IGNORE itself since there is no event-id table here.

    `on_conflict_do_nothing` is a DIALECT-SPECIFIC construct, NOT portable:
    the sqlite-dialect `Insert` does NOT compile against the Postgres
    dialect (verified empirically: `sqlite.insert(...).
    on_conflict_do_nothing().compile(dialect=postgresql.dialect())` raises
    `AttributeError: 'OnConflictDoNothing' object has no attribute
    'constraint_target'`). Tests run sqlite-in-memory
    (`tests/conftest.py:99-101`) but prod is Neon Postgres
    (`app/db/session.py:24-31`), so we pick the construct off the live bind
    dialect — both emit `ON CONFLICT (id) DO NOTHING`. Tier defaults to
    `free` (the model default, `app/db/models.py:123-127`)."""
    org_name = email or f"{user_id}'s workspace"

    # `AsyncSession.bind` is the AsyncEngine; `.dialect.name` is sync-safe
    # (no I/O) and is "postgresql" on Neon / "sqlite" in tests.
    dialect_name = db.bind.dialect.name if db.bind is not None else "sqlite"
    insert = pg_insert if dialect_name == "postgresql" else sqlite_insert

    await db.execute(
        insert(Org)
        .values(id=org_id, name=org_name, tier=OrgTier.free)
        .on_conflict_do_nothing(index_elements=["id"])
    )
    # User row second: `users.org_id` FKs `orgs.id`, so the org must exist
    # first (the org INSERT above is in the SAME tx, flushed by get_session
    # on clean return). email is NOT NULL (`models.py:150`); if Clerk sent no
    # resolvable address we still must provision (the JWT template only needs
    # org_id) — fall back to the user id as a non-null placeholder.
    user_result = await db.execute(
        insert(User)
        .values(id=user_id, email=email or user_id, org_id=org_id)
        .on_conflict_do_nothing(index_elements=["id"])
    )
    await db.flush()
    # rowcount == 1 → the user row was inserted by THIS delivery (fresh
    # sign-up). rowcount == 0 → ON CONFLICT DO NOTHING fired: a duplicate
    # delivery, the user already existed. This is the exactly-once gate for
    # the `signup_completed` event below. (Both pg and sqlite dialects
    # report rowcount for an INSERT...ON CONFLICT DO NOTHING; the org INSERT
    # is intentionally not the anchor — a user could in principle predate
    # its own org row only via a partial prior failure, which get_session's
    # atomic rollback already prevents.)
    return user_result.rowcount == 1


async def _sync_clerk_public_metadata(user_id: str, org_id: str) -> None:
    """Write `public_metadata.org_id` back to Clerk — merge, NOT clobber.

    The brief pins `PATCH /v1/users/{id}`, which FULL-REPLACES
    `public_metadata` (https://clerk.com/docs/reference/backend/user/update-user,
    accessed 2026-05-17). Safety + idempotency are achieved client-side:
      1. GET the user. If `public_metadata.org_id` is ALREADY set → return
         without patching (idempotent: duplicate Clerk deliveries and
         re-runs after a crash do not re-issue the write).
      2. Else PATCH `{**existing_public_metadata, "org_id": org_id}` so any
         pre-existing keys survive the full-replace endpoint.

    Network/Clerk failures propagate (raise) so the whole webhook 5xxs and
    Clerk RETRIES — the DB rows are already committed-or-rolled-back
    atomically by get_session, and step 1's GET makes the retry skip the
    PATCH if a prior attempt actually landed it. We never swallow the error
    into a false 200 (that would strand a user with no JWT-template claim).
    """
    headers = {
        "Authorization": f"Bearer {settings.CLERK_SECRET_KEY}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=_CLERK_API_TIMEOUT_S) as client:
        get_resp = await client.get(f"{_CLERK_API_BASE}/users/{user_id}", headers=headers)
        get_resp.raise_for_status()
        raw_meta = get_resp.json().get("public_metadata")
        # Coerce to a dict up front: Clerk returns an object, but a missing /
        # null / unexpected-shape value must NOT crash the spread below.
        existing_meta = raw_meta if isinstance(raw_meta, dict) else {}
        if existing_meta.get("org_id"):
            # Already provisioned on Clerk's side — nothing to do (idempotent).
            return

        merged = {**existing_meta, "org_id": org_id}
        patch_resp = await client.patch(
            f"{_CLERK_API_BASE}/users/{user_id}",
            headers=headers,
            json={"public_metadata": merged},
        )
        patch_resp.raise_for_status()


# --------------------------------------------------------------------------
# webhook handler — mirrors app/api/billing.py:366-503 structure
# --------------------------------------------------------------------------


@router.post("/webhooks/clerk", include_in_schema=True)
@limiter.limit(WEBHOOK_LIMIT)
async def clerk_webhook(
    request: Request,
    db: AsyncSession = Depends(get_session),
) -> JSONResponse:
    """Clerk lifecycle webhook. Provisions a personal org on `user.created`.

    Control flow parallels the Stripe webhook (`app/api/billing.py:366-503`):
      1. raw bytes (signature is over the raw body, not parsed JSON).
      2. structural header pre-check → clean 400 (vs a 500 traceback leak).
      3. Svix HMAC + timestamp verify → 403 on a well-formed-but-bad triple.
      4. dispatch on event type; non-`user.created` → 200 `ignored` (so
         Clerk does NOT retry-storm an event we deliberately no-op).
      5. provision rows idempotently + sync Clerk public_metadata.
    """
    # Step 1 — raw bytes. Svix signs `f"{svix_id}.{svix_timestamp}.{body}"`;
    # any reparse/reserialization would break the HMAC.
    raw_body = await request.body()

    # Step 2 — D4-harden-equivalent structural gate. Svix omits none of the
    # three on a genuine delivery; absence/garbage is misconfig/forgery/probe,
    # NOT a crypto failure, so it is a 400 (clean envelope, no internal
    # detail) and only a real verification failure on a well-formed triple
    # becomes a 403. We pre-validate here because `Webhook.verify` collapses
    # BOTH into one `WebhookVerificationError` (verified empirically against
    # svix==1.93.0: missing headers → "Missing required headers"; stale ts →
    # "Message timestamp too old" — same exception class). Mirrors
    # `app/api/billing.py:384-409`.
    svix_id = request.headers.get("svix-id")
    svix_timestamp = request.headers.get("svix-timestamp")
    svix_signature = request.headers.get("svix-signature")
    if not _is_well_formed_svix_headers(svix_id, svix_timestamp, svix_signature):
        logger.warning(
            "clerk webhook rejected: missing/malformed svix headers (id=%s ts=%s sig=%s)",
            svix_id is not None,
            svix_timestamp is not None,
            svix_signature is not None,
        )
        return JSONResponse(
            status_code=400,
            content={
                "code": "invalid_request",
                "message": "Missing or malformed Svix signature headers.",
            },
        )

    # Step 3 — HMAC + timestamp-tolerance verify. `Webhook.verify` raises
    # WebhookVerificationError on a bad signature OR a stale timestamp
    # (replay) — header was well-formed (pre-checked) so this is a genuine
    # verification failure → 403, NOT 400. `verify` ALSO returns the parsed
    # JSON payload, so a malformed-JSON-but-correctly-signed body raises the
    # same error here (no separate ValueError branch like Stripe's, because
    # Svix verifies the raw bytes before/while parsing). Log the reason
    # server-side; return only the opaque envelope (no internal detail leak,
    # same discipline as `app/api/billing.py:426-451`).
    try:
        event = Webhook(settings.CLERK_WEBHOOK_SECRET).verify(
            raw_body,
            {
                "svix-id": svix_id or "",
                "svix-timestamp": svix_timestamp or "",
                "svix-signature": svix_signature or "",
            },
        )
    except WebhookVerificationError as exc:
        logger.warning("clerk webhook signature verification failed: %s", exc)
        return JSONResponse(
            status_code=403,
            content={
                "code": "signature_verification_failed",
                "message": "Svix signature verification failed.",
            },
        )

    if not isinstance(event, dict):
        # Defensive: a verified-but-non-object envelope is unusable input.
        logger.warning("clerk webhook: verified payload is not a JSON object")
        return JSONResponse(
            status_code=400,
            content={"code": "invalid_request", "message": "Malformed webhook payload."},
        )

    event_type = event.get("type")

    # Step 4 — only `user.created` provisions. Every other event type is a
    # deliberate no-op acknowledged 200 so Clerk does NOT enter a retry storm
    # on events we don't model (analogue of the Stripe §6 fallthrough,
    # `app/api/billing.py:489-491`).
    if event_type != "user.created":
        return JSONResponse(status_code=200, content={"status": "ignored"})

    data = event.get("data")
    if not isinstance(data, dict):
        return JSONResponse(
            status_code=400,
            content={"code": "invalid_request", "message": "Malformed webhook payload."},
        )
    user_id = data.get("id")
    if not isinstance(user_id, str) or not user_id:
        return JSONResponse(
            status_code=400,
            content={"code": "invalid_request", "message": "Event data missing user id."},
        )

    email = _primary_email(data)
    # Deterministic personal-org id: a pure function of the Clerk user id, so
    # a retried delivery computes the SAME org_id (the idempotency anchor —
    # there is no event-id table; the determinism + ON CONFLICT DO NOTHING IS
    # the idempotency proof).
    org_id = f"org_personal_{user_id}"

    # Step 5a — mirror rows idempotently. get_session() (db/session.py:52-63)
    # commits on clean return / rolls back on raise — same tx posture as the
    # Stripe handler. `newly_provisioned` is True only on the delivery that
    # actually created the user (the exactly-once gate for the funnel event
    # in step 5a').
    newly_provisioned = await _provision_org_and_user(
        db, user_id=user_id, org_id=org_id, email=email
    )

    # Step 5a' — funnel: emit the SERVER-trustworthy `signup_completed`
    # conversion into the org-scoped `usage_events` table, INSIDE this same
    # transaction (so a later step-5b Clerk-API failure rolls the event back
    # with the rows — never a `signup_completed` for a user whose
    # provisioning didn't commit). Org-scoped (we just minted `org_id`), so
    # this is the correct table — NOT `funnel_events` (that is for the
    # anonymous, org-less, client-reported top of the funnel). Gated on
    # `newly_provisioned` so a duplicate Svix delivery does NOT write a
    # second row (mirrors the org/user ON-CONFLICT-DO-NOTHING idempotency;
    # `usage_events` has an autoincrement PK so the gate, not a PK conflict,
    # is what makes it exactly-once). No PII in `meta`. The client cannot
    # forge this — it only exists on the HMAC-verified Svix path.
    if newly_provisioned:
        db.add(
            UsageEvent(
                org_id=org_id,
                kind="signup_completed",
                meta={"source": "clerk.user.created"},
            )
        )
        await db.flush()

    # Step 5b — write the JWT-template source claim back to Clerk
    # (merge-not-clobber; idempotent skip if already set). A Clerk API
    # failure RAISES → get_session rolls back the rows → 5xx → Clerk retries
    # cleanly (the GET-first skip makes the retry converge). We deliberately
    # do NOT 200 on a failed metadata sync: a user whose `public_metadata`
    # lacks `org_id` gets a JWT with no `org_id` claim and is wedged at the
    # middleware's 403 `no_organization` (`app/api/clerk_auth.py:202-212`).
    await _sync_clerk_public_metadata(user_id, org_id)

    return JSONResponse(
        status_code=200,
        content={"status": "provisioned", "org_id": org_id},
    )
