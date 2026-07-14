"""E2E #5 — full Stripe subscription lifecycle.

Layers on top of `tests/test_stripe_webhook_idempotency.py` (8+5 unit
stubs from Cycles G + N) by exercising the full lifecycle: created ->
updated (Solo -> Team upgrade) -> invoice.paid (renewal) ->
invoice.payment_failed (dunning posture) -> deleted (cancellation) ->
post-cancellation access through current_period_end.

This is the "subscription lifecycle correctness" E2E — every transition
is webhook-driven, every webhook must hit the spec §6 control flow, and
the final invariants in DR-008 (tier-gating reads current_period_end,
not just orgs.tier) must hold.

Tested transitions (each is a full webhook round-trip through the
ASGI app with signature verification and idempotency table):
  1. `customer.subscription.created` (Solo)        -> tier=solo
  2. `customer.subscription.updated` (Solo -> Team) -> tier=team
  3. `invoice.paid` (renewal)                       -> no tier change,
     subscriptions.current_period_end updated
  4. `invoice.payment_failed` (card decline)        -> no IMMEDIATE
     downgrade per spec §0 + DR-008. `subscriptions.status` flips to
     `past_due`. orgs.tier stays `team` until Stripe ages out the sub.
  5. `customer.subscription.deleted` (user cancels) -> orgs.tier=free
     immediately at webhook time per spec §2.2, BUT
     `current_period_end` persisted so tier-gating still grants Team
     access through the remaining billing window
     (`stripe_webhook_spec.md` §4.3, `models.py:174-176`).
  6. Post-cancellation access check: a POST /api/v1/jobs with the same
     org+user during the grace window succeeds at Team quotas (200/mo
     per DR-018 #2). After `current_period_end < now()`, the same
     request returns 402.

Race-condition coverage (already pinned in unit stubs):
  - §2.1 out-of-order updated-before-created — unit covers; E2E does
    NOT re-pin (avoid padding per `feedback_no_padding_lists`).
  - §2.3 retry storm — covered in `test_e2e_free_to_paid_upgrade.py::
    test_webhook_duplicate_event_is_no_op_post_tier_flip`.

What this E2E uniquely pins:
  (a) The FULL ordered lifecycle (created -> updated -> paid -> failed
      -> deleted) produces deterministic final state.
  (b) The grace-window read on `current_period_end` works through the
      HTTP layer (tier-gating handler reads the column).
  (c) `subscriptions` row state (status, current_period_end) and
      `orgs.tier` stay aligned across all 5 transitions.
  (d) Outbound `Idempotency-Key` on `stripe.Customer.create` is
      deterministic per Clerk org_id (`stripe_webhook_spec.md` §5.2):
      asserted via mock capture.

Mocking boundary:
  - Clerk JWT: single-org local mint.
  - R2: moto (not exercised on this path beyond auth).
  - Stripe API:
      * `stripe.Customer.create` patched, capture idempotency_key arg.
      * `stripe.Subscription.retrieve` patched per-event to return the
        SDK-shaped Subscription object matching that transition.
      * `stripe.Invoice.retrieve` patched for invoice events.
  - Stripe webhook signature: REAL round-trip via
    `stripe.Webhook.construct_event` and test secret.

Spec sources:
  - `app/api/stripe_webhook_spec.md` §2.1, §2.2, §4.1, §4.3, §5.2, §6
  - DR-003 Solo $50 / Team $250 / Enterprise $1000
  - DR-008 webhook + idempotency table
  - DR-018 default #5 (monthly-only at launch, annual W3+)
  - `models.py:117-127` (Org.tier), `:158-185` (Subscription),
    `:258-273` (StripeEvent)
"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_full_subscription_lifecycle_created_updated_paid_failed_deleted():
    """Single test asserting the full ordered transition produces the
    expected final state. Each step is an HTTP POST to /api/v1/billing/webhook
    with a freshly signed event."""
    # IMPLEMENT IN D5 — depends on D4 Story #7 fully shipped.


@pytest.mark.asyncio
async def test_grace_window_access_after_cancellation():
    """Post-cancellation access: between webhook arrival and
    current_period_end, tier-gating MUST still grant access at the
    paid quota. Pins spec §4.3 invariant."""
    # IMPLEMENT IN D5 — depends on D4 Story #8 reading current_period_end.


@pytest.mark.asyncio
async def test_outbound_customer_create_uses_deterministic_idempotency_key():
    """`stripe.Customer.create` MUST be called with
    `idempotency_key=f"customer-create:{clerk_org_id}"` per
    `stripe_webhook_spec.md` §5.2.

    Pins this through the HTTP layer: trigger checkout twice in
    succession for an org with no stripe_customer_id; assert that
    `stripe.Customer.create` was called twice (or possibly once if the
    handler caches eagerly), and that BOTH calls used the SAME
    idempotency_key string.
    """
    # IMPLEMENT IN D5 — depends on D4 Story #6 lazy-create logic.


@pytest.mark.asyncio
async def test_payment_failed_does_not_immediately_downgrade():
    """Dunning posture: `invoice.payment_failed` MUST NOT flip orgs.tier
    to free. Spec §0 in-scope statement + DR-008 + dunning best practice.
    Subscription.status flips to past_due; tier stays.
    """
    # IMPLEMENT IN D5 — covered by unit stub
    # test_invoice_payment_failed_records_event_no_immediate_downgrade
    # (coverage_expansion.md §3.3); this E2E pins the HTTP-layer wiring.
