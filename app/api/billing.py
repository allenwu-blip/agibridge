"""Stripe billing routes — Story #6 (Checkout + Portal) + Story #7 (webhook).

Authoritative design: `app/api/stripe_webhook_spec.md` (Cycle G). Handler
control flow follows §6 pseudocode EXACTLY (deviation requires a spec
amendment). Stripe-claim citations carry docs.stripe.com URL + access date
2026-05-16; file claims carry `file_path:line`.

Routes:
  POST /api/v1/billing/checkout  — Clerk-JWT-gated. Create a Checkout
        Session for a target tier; lazy-create the Stripe Customer and
        persist `orgs.stripe_customer_id`. Returns the redirect URL.
  POST /api/v1/billing/portal    — Clerk-JWT-gated. Create a Customer
        Portal session for self-serve plan/payment management.
  POST /api/v1/billing/webhook   — Stripe-Signature HMAC ONLY (exempt from
        Clerk middleware at `app/api/clerk_auth.py:62`, verified not
        re-added). Idempotent via `stripe_events.id` PK.

R3: `org_id` is read ONLY from `request.state.org_id` (Clerk-verified),
NEVER from the request body — identical posture to `app/api/jobs.py:13-18`.
The webhook resolves org by `stripe_customer_id` lookup (Stripe is the
caller; there is no JWT), which is still server-trusted state.

Stripe SDK surface verified empirically against `stripe==8.11.0`
(`>=8.0,<9.0`):
  stripe.Webhook.construct_event(payload, sig_header, secret,
                                 tolerance=300, api_key=None)
  raises stripe.SignatureVerificationError on bad sig OR stale timestamp.
  https://docs.stripe.com/webhooks/signatures (accessed 2026-05-16)
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import stripe
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.billing_config import price_id_for_tier, settings, tier_for_price_id
from app.api.rate_limit import WEBHOOK_LIMIT, limiter
from app.db.models import Org, OrgTier, StripeEvent, Subscription, UsageEvent
from app.db.session import get_session

logger = logging.getLogger("agibridge.billing")

router = APIRouter()

# Stripe library default tolerance is 300s (verified:
# `inspect.signature(stripe.Webhook.construct_event)` ==
# `(payload, sig_header, secret, tolerance=300, api_key=None)`).
# We pass it explicitly to pin spec §3.3 regardless of SDK default drift.
_WEBHOOK_TOLERANCE_S = 300


# --------------------------------------------------------------------------
# request / response models
# --------------------------------------------------------------------------


class CheckoutRequest(BaseModel):
    """`tier` is the plan the org wants. `success_url`/`cancel_url` are where
    Stripe redirects post-checkout (frontend supplies its own routes). NO
    org_id field — R3: org comes from the JWT (`jobs.py:13-18` posture)."""

    tier: OrgTier
    success_url: str = Field(..., min_length=1, max_length=2048)
    cancel_url: str = Field(..., min_length=1, max_length=2048)


class RedirectResponse(BaseModel):
    url: str


class PortalRequest(BaseModel):
    return_url: str = Field(..., min_length=1, max_length=2048)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _err(status: int, code: str, message: str, suggestion: str | None = None) -> HTTPException:
    """Same error envelope shape as `jobs.py:127-131` / `clerk_auth.py:214`."""
    return HTTPException(
        status_code=status,
        detail={"code": code, "message": message, "suggestion": suggestion},
    )


def _is_well_formed_sig_header(sig: str | None) -> bool:
    """D4-harden Check 1: structural validity of the `Stripe-Signature`
    header BEFORE the HMAC verify, so a missing/garbage header is a clean
    400 (`invalid_request`) and only a real crypto/timestamp failure on a
    well-formed header becomes a 403 (`signature_verification_failed`).

    Stripe's header is a comma-separated key/value list that ALWAYS carries
    a `t=<unix-seconds>` element and at least one `v1=<hex>` signature
    (https://docs.stripe.com/webhooks/signatures, accessed 2026-05-17).
    We require: non-empty, a `t=` with an all-digit value, and a non-empty
    `v1=` element. This is intentionally looser than the real HMAC check
    (which `stripe.Webhook.construct_event` still performs) — its only job
    is the 400-vs-403 split, never to replace verification."""
    if not sig:
        return False
    parts: dict[str, str] = {}
    for token in sig.split(","):
        k, _, v = token.partition("=")
        k = k.strip()
        if k and k not in parts:  # first value wins (Stripe never repeats t/v1)
            parts[k] = v.strip()
    t = parts.get("t")
    v1 = parts.get("v1")
    return bool(t) and t.isdigit() and bool(v1)


def _require_org(request: Request) -> str:
    """R3 enforcement — org_id ONLY from JWT-populated request.state. Mirror
    of `jobs.py:134-144`; fail loud (500) rather than silently mis-scope."""
    org_id = getattr(request.state, "org_id", None)
    if not isinstance(org_id, str) or not org_id:
        raise _err(500, "auth_state_missing", "Authenticated org not resolved.")
    return org_id


async def _get_org(db: AsyncSession, org_id: str) -> Org:
    org = (await db.execute(select(Org).where(Org.id == org_id))).scalar_one_or_none()
    if org is None:
        # The Clerk org exists (JWT verified) but no mirror row yet. Clerk
        # org-sync (separate webhook, out of D4-B scope) creates it; until
        # then billing can't proceed.
        raise _err(
            404,
            "org_not_provisioned",
            "Your organization isn't fully set up yet.",
            "Retry in a moment; if it persists, contact support.",
        )
    return org


async def _ensure_stripe_customer(db: AsyncSession, org: Org) -> str:
    """Lazy-create the Stripe Customer and persist `orgs.stripe_customer_id`.

    Idempotency-Key is the deterministic Clerk org id per
    `stripe_webhook_spec.md:161-171`: intent is exactly-one-customer-per-org,
    so a retried create with the same key returns the same customer instead
    of forking a duplicate (https://docs.stripe.com/api/idempotent_requests,
    accessed 2026-05-16)."""
    if org.stripe_customer_id:
        return org.stripe_customer_id
    stripe.api_key = settings.STRIPE_SECRET_KEY
    customer = stripe.Customer.create(
        name=org.name,
        metadata={"clerk_org_id": org.id},
        idempotency_key=f"customer-create:{org.id}",
    )
    org.stripe_customer_id = customer["id"]
    await db.flush()
    return customer["id"]


# --------------------------------------------------------------------------
# Story #6 — Checkout + Customer Portal
# --------------------------------------------------------------------------


@router.post("/billing/checkout", response_model=RedirectResponse)
async def create_checkout(
    request: Request,
    body: CheckoutRequest,
    db: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    """Create a subscription Checkout Session for `body.tier`.

    Default: NO trial (`dispatches/D4_specs.md:134` default; override only on
    a ratified 14-day-trial decision). Mode `subscription` because all 3
    plans are recurring (`stripe_webhook_spec.md` §0).
    https://docs.stripe.com/api/checkout/sessions/create (accessed 2026-05-16)
    """
    org_id = _require_org(request)
    if body.tier == OrgTier.free:
        raise _err(
            400,
            "invalid_tier",
            "Free is the default tier; there's nothing to check out.",
            "Pick Solo, Team, or Enterprise.",
        )

    org = await _get_org(db, org_id)
    customer_id = await _ensure_stripe_customer(db, org)
    price_id = price_id_for_tier(body.tier)

    stripe.api_key = settings.STRIPE_SECRET_KEY
    session = stripe.checkout.Session.create(
        mode="subscription",
        customer=customer_id,
        line_items=[{"price": price_id, "quantity": 1}],
        success_url=body.success_url,
        cancel_url=body.cancel_url,
        # Carried back on the resulting subscription so a future audit can
        # tie a Stripe sub to a Clerk org without an extra lookup.
        subscription_data={"metadata": {"clerk_org_id": org_id}},
    )
    return RedirectResponse(url=str(session["url"]))


@router.post("/billing/portal", response_model=RedirectResponse)
async def create_portal(
    request: Request,
    body: PortalRequest,
    db: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    """Customer Portal session — self-serve upgrade/downgrade/cancel/card.
    https://docs.stripe.com/api/customer_portal/sessions/create
    (accessed 2026-05-16)."""
    org_id = _require_org(request)
    org = await _get_org(db, org_id)
    if not org.stripe_customer_id:
        raise _err(
            409,
            "no_stripe_customer",
            "No billing account yet — start a subscription first.",
            "Use Checkout to pick a plan, then manage it here.",
        )
    stripe.api_key = settings.STRIPE_SECRET_KEY
    session = stripe.billing_portal.Session.create(
        customer=org.stripe_customer_id,
        return_url=body.return_url,
    )
    return RedirectResponse(url=str(session["url"]))


# --------------------------------------------------------------------------
# Story #7 — webhook handler.  Follows stripe_webhook_spec.md §6 EXACTLY.
# --------------------------------------------------------------------------


def _sub_period_end(sub: stripe.Subscription) -> datetime:
    """`current_period_end` is a Unix ts on the Stripe Subscription
    (https://docs.stripe.com/api/subscriptions/object, accessed 2026-05-16).
    Fall back to now() if absent (defensive — Stripe always sends it on an
    active sub)."""
    raw = sub.get("current_period_end")
    if isinstance(raw, int):
        return datetime.fromtimestamp(raw, tz=UTC)
    return datetime.now(UTC)


def _tier_from_subscription(sub: stripe.Subscription) -> OrgTier:
    """Source-of-truth tier from the RE-FETCHED Subscription (§4.1), not the
    webhook payload. `status` is the canonical lifecycle field
    (https://docs.stripe.com/api/subscriptions/object, accessed 2026-05-16):
    a canceled / unpaid / incomplete_expired sub maps to `free` regardless
    of which price it carried (§2.2)."""
    status = sub.get("status")
    if status in {"canceled", "incomplete_expired", "unpaid"}:
        return OrgTier.free
    items = (sub.get("items") or {}).get("data") or []
    if not items:
        return OrgTier.free
    price = (items[0] or {}).get("price") or {}
    price_id = price.get("id")
    if not isinstance(price_id, str):
        return OrgTier.free
    return tier_for_price_id(price_id)


async def _org_for_customer(db: AsyncSession, customer_id: str | None) -> Org | None:
    if not customer_id:
        return None
    return (
        await db.execute(select(Org).where(Org.stripe_customer_id == customer_id))
    ).scalar_one_or_none()


async def _apply_subscription_state(db: AsyncSession, sub: stripe.Subscription) -> None:
    """Flip `orgs.tier` + UPSERT the `subscriptions` mirror from the
    re-fetched Subscription (§2.1, §2.2, §4.1). On cancel: tier->free but
    `current_period_end` is PRESERVED so tier-gating grants access until the
    paid period actually ends (§4.3, `models.py:183-186`)."""
    customer_id = sub.get("customer")
    if isinstance(customer_id, dict):  # expanded customer object
        customer_id = customer_id.get("id")
    org = await _org_for_customer(db, customer_id)
    if org is None:
        # Webhook for a customer we don't mirror (e.g. created out-of-band).
        # Audit row already inserted by the caller; nothing to mutate.
        logger.warning("stripe webhook: no org for customer %s", customer_id)
        return

    tier = _tier_from_subscription(sub)
    period_end = _sub_period_end(sub)
    status = str(sub.get("status") or "unknown")

    org.tier = tier

    sub_id = str(sub["id"])
    existing = (
        await db.execute(select(Subscription).where(Subscription.id == sub_id))
    ).scalar_one_or_none()
    if existing is None:
        db.add(
            Subscription(
                id=sub_id,
                org_id=org.id,
                stripe_customer_id=str(customer_id),
                tier=tier,
                status=status,
                current_period_end=period_end,
            )
        )
        # Funnel: the FIRST time we mirror this Stripe subscription id is the
        # checkout-completed moment — a renewal/plan-change reuses the same
        # `sub_id` so `existing is not None` and we do NOT re-emit. Only a
        # PAID tier counts as a conversion (a sub that lands straight in
        # `free` — canceled/unpaid/incomplete_expired per
        # `_tier_from_subscription`, billing.py:254-270 — is not a sale).
        # SERVER-trustworthy + org-scoped → `usage_events`, NOT
        # `funnel_events`. Idempotency is the existing `stripe_events.id` PK
        # short-circuit (billing.py:461-473): the whole side-effect set,
        # this row included, applies at most once per Stripe event; the
        # `existing is None` guard additionally suppresses a re-emit across
        # DISTINCT events for the same subscription. No PII in `meta`.
        if tier != OrgTier.free:
            db.add(
                UsageEvent(
                    org_id=org.id,
                    kind="checkout_completed",
                    meta={"tier": tier.value, "source": "stripe.subscription"},
                )
            )
    else:
        existing.tier = tier
        existing.status = status
        existing.current_period_end = period_end
    await db.flush()


async def _record_invoice_paid(db: AsyncSession, inv: stripe.Invoice) -> None:
    """`invoice.paid` does NOT drive `orgs.tier` (§2.2): a paid invoice for
    an already-canceled sub must not resurrect a paid tier. Record an
    accounting `usage_events` row keyed by org for the dashboard."""
    org = await _org_for_customer(db, inv.get("customer"))
    if org is None:
        return
    db.add(
        UsageEvent(
            org_id=org.id,
            kind="invoice_paid",
            meta={
                "invoice_id": str(inv.get("id")),
                "amount_paid": inv.get("amount_paid"),
                "currency": inv.get("currency"),
            },
        )
    )
    await db.flush()


async def _flag_payment_failure(db: AsyncSession, inv: stripe.Invoice) -> None:
    """`invoice.payment_failed`: do NOT downgrade here (§0 scope + DR-008
    dunning posture). The downgrade is Stripe's call — it later emits
    `customer.subscription.{updated->unpaid,deleted}` which `_apply_*`
    handles. We only record the failure for dashboard surfacing."""
    org = await _org_for_customer(db, inv.get("customer"))
    if org is None:
        return
    db.add(
        UsageEvent(
            org_id=org.id,
            kind="invoice_payment_failed",
            meta={
                "invoice_id": str(inv.get("id")),
                "attempt_count": inv.get("attempt_count"),
            },
        )
    )
    await db.flush()


@router.post("/billing/webhook", include_in_schema=True)
@limiter.limit(WEBHOOK_LIMIT)
async def stripe_webhook(
    request: Request,
    db: AsyncSession = Depends(get_session),
) -> JSONResponse:
    """Stripe subscription-lifecycle webhook. Control flow is
    `stripe_webhook_spec.md` §6 pseudocode, step-for-step.

    Idempotency invariant (§1): for any `event.id`, the side-effect set is
    applied at most once. The `stripe_events.id` PK INSERT lives INSIDE the
    same tx as the state mutation (§6 "why step 5 is INSIDE the tx") — a
    crash rolls BOTH back, so a Stripe retry reprocesses cleanly instead of
    short-circuiting on a "seen" row whose state never landed.
    """
    # §6 step 1 — raw bytes, NOT parsed JSON (signature is over raw body, §3.2).
    raw_body = await request.body()

    # §6 step 2 — D4-harden Check 1: clean rejection on a PUBLIC unauthenticated
    # endpoint. Stripe never omits this header AND always formats it
    # `t=<unix>,v1=<hex>[,v0=...]` (https://docs.stripe.com/webhooks/signatures,
    # accessed 2026-05-17). Absence OR a structurally-invalid header is a
    # misconfig/forgery/abuse-probe, not a crypto failure — distinguish it
    # from a genuine signature mismatch so we can return the precise status
    # (400 malformed-input vs 403 verification-failed) instead of a 500 that
    # leaks an internal traceback. We pre-validate the structure here because
    # stripe.WebhookSignature.verify_header collapses BOTH cases into a single
    # SignatureVerificationError (verified empirically against stripe==8.11.0:
    # `verify_header` wraps the header-parse failure in
    # SignatureVerificationError "Unable to extract timestamp and
    # signatures"). Cheap structural gate keeps the 400/403 split crisp.
    sig = request.headers.get("stripe-signature")
    if not _is_well_formed_sig_header(sig):
        logger.warning(
            "stripe webhook rejected: missing/malformed Stripe-Signature header (present=%s)",
            sig is not None,
        )
        return JSONResponse(
            status_code=400,
            content={
                "code": "invalid_request",
                "message": "Missing or malformed Stripe-Signature header.",
            },
        )

    # §6 step 3 — HMAC + timestamp-tolerance verify. construct_event raises
    # stripe.SignatureVerificationError on a bad v1 HMAC OR a timestamp
    # outside the 300s tolerance (§3.3, §3.4 layer 1) and json.JSONDecodeError
    # (⊂ ValueError) on a malformed JSON body — verified empirically against
    # stripe==8.11.0 (`json.loads(payload, ...)` inside construct_event, after
    # verify_header). Pre-D4-harden the ValueError branch was UNCAUGHT → 500
    # on a signed-but-garbage body; that is the real public-surface info-leak
    # this check closes.
    try:
        event = stripe.Webhook.construct_event(
            payload=raw_body,
            sig_header=sig,
            secret=settings.STRIPE_WEBHOOK_SECRET,
            tolerance=_WEBHOOK_TOLERANCE_S,
        )
    except stripe.SignatureVerificationError as exc:
        # Bad v1 HMAC or stale timestamp (replay). Header was well-formed
        # (pre-checked above) so this is a genuine verification failure →
        # 403, NOT 400. Log the full Stripe-supplied reason server-side;
        # return only the opaque envelope (no internal detail leak).
        logger.warning("stripe webhook signature verification failed: %s", exc)
        return JSONResponse(
            status_code=403,
            content={
                "code": "signature_verification_failed",
                "message": "Stripe signature verification failed.",
            },
        )
    except ValueError as exc:
        # Malformed JSON body (json.JSONDecodeError ⊂ ValueError) AFTER a
        # passing signature check — e.g. a truncated/corrupted but correctly
        # signed payload. Client-side defect → 400, not 500. Detail logged
        # server-side only.
        logger.warning("stripe webhook rejected: malformed JSON body (%s)", exc)
        return JSONResponse(
            status_code=400,
            content={
                "code": "invalid_request",
                "message": "Malformed webhook payload.",
            },
        )

    event_id = event["id"]
    event_type = event["type"]

    # §6 steps 4-8 — SINGLE transaction. get_session() commits on clean
    # return, rolls back on any raised exception (`db/session.py:57-63`).
    # §6 step 5 — event-row INSERT FIRST. flush() surfaces the PK collision
    # NOW (not at commit) so a duplicate is a fast 200 with zero side effects
    # and zero Stripe API re-fetch.
    db.add(StripeEvent(id=event_id, type=event_type, payload=event))
    try:
        await db.flush()
    except IntegrityError:
        # §2.3 retry storm / §2.4 response-drop / §2.5 dashboard resend —
        # all converge here. Roll back the failed INSERT savepoint and
        # short-circuit; the original processing already applied (or rolled
        # back atomically) the side effects.
        await db.rollback()
        return JSONResponse(
            status_code=200,
            content={"status": "duplicate", "event_id": event_id},
        )

    # §6 step 6 — re-fetch the authoritative object via the Stripe API
    # (§4.1). The event PAYLOAD is used ONLY to extract the object id; state
    # comes from the live API so out-of-order deliveries converge (§2.1).
    stripe.api_key = settings.STRIPE_SECRET_KEY
    if event_type.startswith("customer.subscription."):
        sub_id = event["data"]["object"]["id"]
        sub = stripe.Subscription.retrieve(sub_id, expand=["items.data.price"])
        await _apply_subscription_state(db, sub)
    elif event_type == "invoice.paid":
        inv = stripe.Invoice.retrieve(event["data"]["object"]["id"])
        await _record_invoice_paid(db, inv)
    elif event_type == "invoice.payment_failed":
        inv = stripe.Invoice.retrieve(event["data"]["object"]["id"])
        await _flag_payment_failure(db, inv)
    # else: row inserted for audit, no mutation (§6 step 6 fallthrough). We
    # still set processed_at below so the §6 stuck-handler tripwire
    # (received_at < now-5m AND processed_at IS NULL) does NOT fire forever.

    # §6 step 7 — mark processed inside the SAME tx.
    ev_row = (await db.execute(select(StripeEvent).where(StripeEvent.id == event_id))).scalar_one()
    ev_row.processed_at = datetime.now(UTC)
    await db.flush()

    # §6 step 8 — COMMIT happens in get_session()'s clean-return path.
    # §6 step 9 — processed acknowledgement.
    return JSONResponse(
        status_code=200,
        content={"status": "processed", "event_id": event_id},
    )
