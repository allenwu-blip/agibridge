"""E2E #2 — free user upgrades to Solo via Stripe Checkout.

Pins the conversion path that determines first-paying-customer success
(DR-014: 1 paying customer = minimum success gate). Exercises the
free-tier soft cap collision -> upgrade prompt -> Stripe Checkout
redirect -> webhook arrival -> orgs.tier flip -> subsequent job runs
with paid-tier gate.

Flow:
  1. Free user hits soft cap (5/mo per DR-018 #2) on 6th job creation
     -> backend returns 402 Payment Required (D4 Story #8) + triggers
     `upgrade-prompt.md` Resend email (cross-brief #5).
  2. User clicks Upgrade -> POST /api/v1/billing/checkout -> backend
     creates Stripe Checkout session and returns redirect URL
     (D4 Story #6).
  3. (Mocked) Stripe Checkout completes -> Stripe POSTs
     `customer.subscription.created` to /api/v1/billing/webhook.
  4. Webhook handler verifies signature (spec §3.2), inserts
     stripe_events row (spec §1), re-fetches Subscription (spec §4.1),
     flips orgs.tier=solo (spec §6 step 6).
  5. User submits a 50-episode dataset; backend now accepts (Solo cap
     50/mo per DR-018 #2) and subprocess runs WITHOUT `--max-episodes 1`.

Cross-module surfaces:
  - Clerk JWT middleware (D4 Story #2)
  - app/api/billing.py {checkout, webhook}  (D4 Story #6, #7)
  - Stripe SDK (`stripe>=8.0,<9.0` per stripe_webhook_spec.md §6.4)
  - StripeEvent + Subscription + Org.tier (models.py:117-127, 158-185, 258-273)
  - Tier-gating in upload.py (D4 Story #8)

Mocking boundary:
  - Clerk JWT: local RSA mint (test fixture).
  - R2: moto.
  - Stripe Checkout session creation: `unittest.mock.patch(
    "stripe.checkout.Session.create")` returning a fake `url`.
  - Stripe Subscription.retrieve: patched to return Solo-tier object
    matching spec §4.1 `expand=["items.data.price"]` shape.
  - Webhook signature: REAL `stripe.Webhook.construct_event` round-trip
    using `webhook_secret` fixture (spec §3.2, mirrors
    `tests/test_stripe_webhook_idempotency.py:65-68`).

Spec sources:
  - `app/api/stripe_webhook_spec.md` §1, §4.1, §6 — handler contract
  - `dispatches/D4_specs.md` §4 Story #6, #7, #8 — backend tasks
  - DR-003 — Solo $50/mo locked
  - DR-018 default #2 — soft-cap ladder
"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_soft_cap_hit_returns_402_and_emits_upgrade_email():
    """Free org with 5 prior jobs this billing period -> 6th POST /jobs
    returns 402 Payment Required and triggers `upgrade-prompt.md` via
    Resend (cross-brief #5).

    Assertions:
      - Response status == 402
      - Response body identifies the limit + tier
      - Resend was called once with template_id corresponding to
        `upgrade-prompt.md` (patch `resend.Emails.send`)
      - No job row was created (JobStore counts unchanged)
    """
    # IMPLEMENT IN D5 — depends on D4 Story #8 + email dispatch.


@pytest.mark.asyncio
async def test_checkout_to_webhook_to_tier_flip():
    """Full upgrade round-trip.

    Steps under test:
      1. POST /api/v1/billing/checkout with auth -> 200 + redirect_url
      2. Simulate Stripe webhook delivery: build raw_body for
         `customer.subscription.created` with tier_id matching Solo,
         sign it via test secret, POST to /api/v1/billing/webhook.
      3. Assert response == 200 with body status='processed'
      4. Assert stripe_events row exists with processed_at NOT NULL
         (spec §6 step 7)
      5. Assert orgs.tier == 'solo'
      6. Assert subscriptions row upserted with current_period_end set
         (spec §4.3, models.py:174-176)
      7. Subsequent POST /api/v1/jobs with the same org succeeds, and
         subprocess command does NOT contain `--max-episodes 1`.
    """
    # IMPLEMENT IN D5 — depends on D4 Story #6 + #7 + Refactor #3.


@pytest.mark.asyncio
async def test_webhook_duplicate_event_is_no_op_post_tier_flip():
    """After a successful upgrade, Stripe redelivers the same event_id
    (per spec §2.3 retry storm). The 2nd delivery must return 200 with
    body status='duplicate' and orgs.tier MUST remain solo (no transient
    re-flip).

    Layered with `test_stripe_webhook_idempotency.py` unit-level stubs;
    this E2E variant exercises the full route + middleware exemption
    (`stripe_webhook_spec.md:240`).
    """
    # IMPLEMENT IN D5 — depends on D4 Story #7 + middleware exemption.
