"""Story #6 — Stripe Checkout + Customer Portal.

Pins the contract (`dispatches/D4_specs.md:130-135`):
- POST /api/v1/billing/checkout — Clerk-JWT-gated, lazy-creates the Stripe
  Customer (persisting `orgs.stripe_customer_id`), returns the redirect URL.
  NO trial by default. Rejects `tier=free`.
- POST /api/v1/billing/portal — Clerk-JWT-gated Customer Portal session.

R3: org comes from the JWT only (`clerk_token` fixture mints it into the
claim); the request body never carries org_id.

Stripe is fully mocked (`unittest.mock.patch`) — no live API. Auth uses the
same fake-JWKS app wiring as `tests/conftest.py:db_app`.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.db.models import Org


@pytest.fixture(autouse=True)
def _stripe_env(monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_dummy")
    monkeypatch.setenv("STRIPE_PUBLISHABLE_KEY", "pk_test_dummy")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test")
    monkeypatch.setenv("STRIPE_PRICE_SOLO", "price_TEST_solo")
    monkeypatch.setenv("STRIPE_PRICE_TEAM", "price_TEST_team")
    monkeypatch.setenv("STRIPE_PRICE_ENTERPRISE", "price_TEST_ent")


async def _seed_org(monkeypatch, *, customer_id: str | None = None) -> None:
    """Insert org_TEST into the SAME in-memory engine the db_app fixture
    patched into app.db.session."""
    import app.db.session as ds

    sm = ds._sessionmaker
    assert sm is not None
    async with sm() as s:
        s.add(Org(id="org_TEST", name="Test Labs", stripe_customer_id=customer_id))
        await s.commit()


@pytest.mark.asyncio
async def test_checkout_lazy_creates_customer_and_returns_url(db_app, clerk_token, monkeypatch):
    await _seed_org(monkeypatch, customer_id=None)
    token = clerk_token(org_id="org_TEST", user_id="user_A")

    fake_customer = {"id": "cus_NEW"}
    fake_session = {"id": "cs_test_1", "url": "https://checkout.stripe.com/c/pay/cs_test_1"}

    transport = ASGITransport(app=db_app)
    with (
        patch("stripe.Customer.create", return_value=fake_customer) as m_cust,
        patch("stripe.checkout.Session.create", return_value=fake_session) as m_sess,
    ):
        async with AsyncClient(transport=transport, base_url="http://t") as c:
            r = await c.post(
                "/api/v1/billing/checkout",
                json={
                    "tier": "solo",
                    "success_url": "https://app.agibridge.dev/ok",
                    "cancel_url": "https://app.agibridge.dev/no",
                },
                headers={"Authorization": f"Bearer {token}"},
            )
    assert r.status_code == 200
    assert r.json()["url"] == fake_session["url"]
    # deterministic idempotency key per stripe_webhook_spec.md:161-171
    assert m_cust.call_args.kwargs["idempotency_key"] == "customer-create:org_TEST"
    # subscription mode + the solo price id
    assert m_sess.call_args.kwargs["mode"] == "subscription"
    assert m_sess.call_args.kwargs["line_items"][0]["price"] == "price_TEST_solo"
    # NO trial by default (dispatches/D4_specs.md:134)
    assert "subscription_data" in m_sess.call_args.kwargs
    assert "trial_period_days" not in m_sess.call_args.kwargs.get("subscription_data", {})

    # stripe_customer_id persisted on the org
    import app.db.session as ds

    async with ds._sessionmaker() as s:
        org = (await s.execute(select(Org).where(Org.id == "org_TEST"))).scalar_one()
        assert org.stripe_customer_id == "cus_NEW"


@pytest.mark.asyncio
async def test_checkout_reuses_existing_customer(db_app, clerk_token, monkeypatch):
    await _seed_org(monkeypatch, customer_id="cus_EXISTING")
    token = clerk_token(org_id="org_TEST", user_id="user_A")
    transport = ASGITransport(app=db_app)
    with (
        patch("stripe.Customer.create") as m_cust,
        patch(
            "stripe.checkout.Session.create",
            return_value={"id": "cs_2", "url": "https://stripe/cs_2"},
        ) as m_sess,
    ):
        async with AsyncClient(transport=transport, base_url="http://t") as c:
            r = await c.post(
                "/api/v1/billing/checkout",
                json={
                    "tier": "team",
                    "success_url": "https://x/ok",
                    "cancel_url": "https://x/no",
                },
                headers={"Authorization": f"Bearer {token}"},
            )
    assert r.status_code == 200
    m_cust.assert_not_called()  # existing customer reused, no fork
    assert m_sess.call_args.kwargs["customer"] == "cus_EXISTING"


@pytest.mark.asyncio
async def test_checkout_rejects_free_tier(db_app, clerk_token, monkeypatch):
    await _seed_org(monkeypatch, customer_id="cus_X")
    token = clerk_token(org_id="org_TEST", user_id="user_A")
    transport = ASGITransport(app=db_app)
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.post(
            "/api/v1/billing/checkout",
            json={"tier": "free", "success_url": "https://x/o", "cancel_url": "https://x/n"},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "invalid_tier"


@pytest.mark.asyncio
async def test_checkout_requires_auth(db_app):
    """No Bearer token → Clerk middleware 401 (route never runs)."""
    transport = ASGITransport(app=db_app)
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.post(
            "/api/v1/billing/checkout",
            json={"tier": "solo", "success_url": "https://x/o", "cancel_url": "https://x/n"},
        )
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_portal_returns_url(db_app, clerk_token, monkeypatch):
    await _seed_org(monkeypatch, customer_id="cus_PORTAL")
    token = clerk_token(org_id="org_TEST", user_id="user_A")
    transport = ASGITransport(app=db_app)
    with patch(
        "stripe.billing_portal.Session.create",
        return_value={"id": "bps_1", "url": "https://billing.stripe.com/p/session/bps_1"},
    ) as m:
        async with AsyncClient(transport=transport, base_url="http://t") as c:
            r = await c.post(
                "/api/v1/billing/portal",
                json={"return_url": "https://app.agibridge.dev/settings"},
                headers={"Authorization": f"Bearer {token}"},
            )
    assert r.status_code == 200
    assert r.json()["url"].startswith("https://billing.stripe.com/")
    assert m.call_args.kwargs["customer"] == "cus_PORTAL"


@pytest.mark.asyncio
async def test_portal_without_customer_409(db_app, clerk_token, monkeypatch):
    await _seed_org(monkeypatch, customer_id=None)
    token = clerk_token(org_id="org_TEST", user_id="user_A")
    transport = ASGITransport(app=db_app)
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.post(
            "/api/v1/billing/portal",
            json={"return_url": "https://x/s"},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "no_stripe_customer"
