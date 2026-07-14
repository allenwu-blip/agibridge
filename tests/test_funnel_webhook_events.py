"""Server-trustworthy funnel conversions emitted INSIDE the existing
webhook transactions:

  - `signup_completed`   ← Clerk `user.created` webhook (`app/api/webhooks.py`)
  - `checkout_completed` ← Stripe subscription webhook (`app/api/billing.py`)

Both land in the org-scoped `usage_events` table (NOT `funnel_events` —
that is the anonymous client surface). The client cannot forge these: they
exist only on the HMAC/Svix-verified webhook paths. They must be
exactly-once under duplicate delivery, mirroring each webhook's existing
idempotency discipline.

Parity target / infra reuse: this file deliberately mirrors
`tests/test_clerk_webhook_provisioning.py` (Svix signing + Clerk-API mock)
and `tests/test_stripe_webhook_idempotency.py` (Stripe HMAC signing +
`stripe.Subscription.retrieve` patch), same in-memory sqlite posture
(`tests/conftest.py:99-101`).

Test → behavior mapping:

| Test                                                       | Asserts                          |
|------------------------------------------------------------|----------------------------------|
| test_signup_completed_emitted_on_user_created              | 1 usage_events signup row, org   |
| test_signup_completed_idempotent_on_duplicate_delivery     | duplicate Svix → still exactly 1 |
| test_checkout_completed_emitted_on_first_paid_subscription | 1 usage_events checkout row      |
| test_checkout_completed_not_emitted_for_free_tier_sub      | canceled/free sub → no row       |
| test_checkout_completed_idempotent_on_duplicate_event      | duplicate Stripe evt → exactly 1 |
| test_checkout_completed_not_reemitted_on_plan_change       | 2nd event same sub → no 2nd row  |
"""

from __future__ import annotations

import hashlib
import hmac
import importlib
import json
import time
from datetime import UTC, datetime
from unittest.mock import patch

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from svix.webhooks import Webhook

from app.db.models import Base, Org, UsageEvent

CLERK_WEBHOOK_SECRET = "whsec_dGVzdHNlY3JldGZvcnVuaXR0ZXN0c29ubHk="  # test-only
CLERK_SECRET_KEY = "sk_test_clerk_dummy"
STRIPE_WEBHOOK_SECRET = "whsec_test_only_DO_NOT_USE_IN_PROD"
SOLO_PRICE = "price_TEST_solo"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    """The EXACT env names the handlers read (mirrors the Clerk + Stripe
    suites' env fixtures). Prod values are HF/GH secrets."""
    monkeypatch.setenv("CLERK_WEBHOOK_SECRET", CLERK_WEBHOOK_SECRET)
    monkeypatch.setenv("CLERK_SECRET_KEY", CLERK_SECRET_KEY)
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_dummy")
    monkeypatch.setenv("STRIPE_PUBLISHABLE_KEY", "pk_test_dummy")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", STRIPE_WEBHOOK_SECRET)
    monkeypatch.setenv("STRIPE_PRICE_SOLO", SOLO_PRICE)
    monkeypatch.setenv("STRIPE_PRICE_TEAM", "price_TEST_team")
    monkeypatch.setenv("STRIPE_PRICE_ENTERPRISE", "price_TEST_enterprise")


@pytest_asyncio.fixture
async def engine_sm():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    yield engine, sm
    await engine.dispose()


@pytest.fixture
def session(engine_sm):
    _engine, sm = engine_sm

    def _factory():
        return sm()

    return _factory


@pytest_asyncio.fixture
async def client(engine_sm, monkeypatch):
    engine, sm = engine_sm
    import app.db.session as db_session

    monkeypatch.setattr(db_session, "_engine", engine)
    monkeypatch.setattr(db_session, "_sessionmaker", sm)

    import app.main as main_mod

    importlib.reload(main_mod)
    transport = ASGITransport(app=main_mod.app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        yield c


async def _count(session, model, where=None) -> int:
    async with session() as s:
        stmt = select(func.count()).select_from(model)
        if where is not None:
            stmt = stmt.where(where)
        return int((await s.execute(stmt)).scalar_one() or 0)


# ----------------------- Clerk: signup_completed -----------------------


def _svix_sign(payload: dict, *, msg_id: str = "msg_test", t: int | None = None):
    """Sign EXACTLY as Svix does (mirrors
    test_clerk_webhook_provisioning._sign:105-118)."""
    raw = json.dumps(payload)
    ts = t if t is not None else int(time.time())
    sig = Webhook(CLERK_WEBHOOK_SECRET).sign(msg_id, datetime.fromtimestamp(ts, UTC), raw)
    return raw.encode(), {
        "svix-id": msg_id,
        "svix-timestamp": str(ts),
        "svix-signature": sig,
    }


def _user_created_evt(user_id="user_NEW", email="ada@example.com"):
    return {
        "type": "user.created",
        "object": "event",
        "data": {
            "id": user_id,
            "email_addresses": [{"id": "idn_1", "email_address": email}],
            "primary_email_address_id": "idn_1",
        },
    }


class _FakeClerkAPI:
    """GET/PATCH stub (mirrors test_clerk_webhook_provisioning._FakeClerkAPI).
    `get_meta` is what GET returns; default {} → PATCH path taken."""

    def __init__(self, get_meta=None):
        self._get_meta = get_meta if get_meta is not None else {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, headers=None):
        return httpx.Response(
            200,
            json={"id": "user_NEW", "public_metadata": self._get_meta},
            request=httpx.Request("GET", url),
        )

    async def patch(self, url, headers=None, json=None):
        return httpx.Response(
            200,
            json={"id": "user_NEW", "public_metadata": json["public_metadata"]},
            request=httpx.Request("PATCH", url),
        )


def _mock_clerk(fake):
    return patch("app.api.webhooks.httpx.AsyncClient", return_value=fake)


async def _post_clerk(client, raw, headers):
    return await client.post(
        "/api/v1/webhooks/clerk",
        content=raw,
        headers={**headers, "content-type": "application/json"},
    )


@pytest.mark.asyncio
async def test_signup_completed_emitted_on_user_created(client, session):
    """A valid `user.created` → exactly one `usage_events` row
    kind=`signup_completed`, org-scoped to the freshly-minted personal org,
    with NO PII in meta (only a static source tag)."""
    ev = _user_created_evt("user_S1", "s1@example.com")
    with _mock_clerk(_FakeClerkAPI(get_meta={})):
        r = await _post_clerk(client, *_svix_sign(ev))
    assert r.status_code == 200

    async with session() as s:
        rows = (
            (await s.execute(select(UsageEvent).where(UsageEvent.kind == "signup_completed")))
            .scalars()
            .all()
        )
    assert len(rows) == 1
    assert rows[0].org_id == "org_personal_user_S1"
    assert rows[0].meta == {"source": "clerk.user.created"}
    # No PII: meta carries no email/user id.
    assert "s1@example.com" not in json.dumps(rows[0].meta)


@pytest.mark.asyncio
async def test_signup_completed_idempotent_on_duplicate_delivery(client, session):
    """A duplicate Svix delivery (retry / dashboard resend) must NOT write a
    second `signup_completed` — `usage_events` has an autoincrement PK so the
    `newly_provisioned` gate (NOT a PK conflict) is the exactly-once anchor,
    mirroring the org/user ON-CONFLICT-DO-NOTHING idempotency."""
    ev = _user_created_evt("user_DUP", "dup@example.com")
    with _mock_clerk(_FakeClerkAPI(get_meta={})):
        r1 = await _post_clerk(client, *_svix_sign(ev))
    assert r1.status_code == 200
    # 2nd delivery: org_id already on Clerk → metadata PATCH skipped, and
    # the user row already exists → newly_provisioned False → no 2nd event.
    with _mock_clerk(_FakeClerkAPI(get_meta={"org_id": "org_personal_user_DUP"})):
        r2 = await _post_clerk(client, *_svix_sign(ev, msg_id="msg_test2"))
    assert r2.status_code == 200

    assert await _count(session, UsageEvent, UsageEvent.kind == "signup_completed") == 1


# ----------------------- Stripe: checkout_completed -----------------------


def _stripe_sign(payload: dict, *, t: int | None = None):
    """v1 = HMAC-SHA256(secret, f"{t}.{raw}") — mirrors
    test_stripe_webhook_idempotency._sign:146-153."""
    raw = json.dumps(payload).encode()
    ts = t if t is not None else int(time.time())
    sig = hmac.new(
        STRIPE_WEBHOOK_SECRET.encode(), f"{ts}.".encode() + raw, hashlib.sha256
    ).hexdigest()
    return raw, f"t={ts},v1={sig}"


def _sub_obj(sub_id="sub_X", *, status="active", price_id=SOLO_PRICE, customer="cus_TEST123"):
    return {
        "id": sub_id,
        "status": status,
        "customer": customer,
        "current_period_end": int(time.time()) + 30 * 86400,
        "items": {"data": [{"price": {"id": price_id}}]},
    }


def _evt(event_id, event_type, obj):
    return {"id": event_id, "type": event_type, "data": {"object": obj}}


async def _post_stripe(client, raw, sig_header):
    return await client.post(
        "/api/v1/billing/webhook",
        content=raw,
        headers={"stripe-signature": sig_header, "content-type": "application/json"},
    )


@pytest_asyncio.fixture
async def org(session):
    async with session() as s:
        s.add(Org(id="org_TEST", name="Test Labs", stripe_customer_id="cus_TEST123"))
        await s.commit()


@pytest.mark.asyncio
async def test_checkout_completed_emitted_on_first_paid_subscription(client, session, org):
    """First `customer.subscription.created` for a PAID tier → exactly one
    `usage_events` row kind=`checkout_completed`, org-scoped, meta carries
    the tier (non-PII)."""
    ev = _evt("evt_CK1", "customer.subscription.created", {"id": "sub_X"})
    with patch("stripe.Subscription.retrieve", return_value=_sub_obj()):
        r = await _post_stripe(client, *_stripe_sign(ev))
    assert r.status_code == 200

    async with session() as s:
        rows = (
            (await s.execute(select(UsageEvent).where(UsageEvent.kind == "checkout_completed")))
            .scalars()
            .all()
        )
    assert len(rows) == 1
    assert rows[0].org_id == "org_TEST"
    assert rows[0].meta == {"tier": "solo", "source": "stripe.subscription"}


@pytest.mark.asyncio
async def test_checkout_completed_not_emitted_for_free_tier_sub(client, session, org):
    """A subscription that resolves to `free` (canceled/unpaid) is NOT a
    sale → NO `checkout_completed` row even though the sub row is mirrored."""
    ev = _evt("evt_CK2", "customer.subscription.created", {"id": "sub_Y"})
    with patch("stripe.Subscription.retrieve", return_value=_sub_obj(status="canceled")):
        r = await _post_stripe(client, *_stripe_sign(ev))
    assert r.status_code == 200
    assert await _count(session, UsageEvent, UsageEvent.kind == "checkout_completed") == 0


@pytest.mark.asyncio
async def test_checkout_completed_idempotent_on_duplicate_event(client, session, org):
    """Duplicate Stripe event id (retry storm, §2.3) → the existing
    `stripe_events.id` PK short-circuit means the whole side-effect set,
    `checkout_completed` included, applies at most once."""
    ev = _evt("evt_CK3", "customer.subscription.created", {"id": "sub_Z"})
    with patch("stripe.Subscription.retrieve", return_value=_sub_obj(sub_id="sub_Z")):
        r1 = await _post_stripe(client, *_stripe_sign(ev))
        r2 = await _post_stripe(client, *_stripe_sign(ev))  # same event id
    assert r1.json()["status"] == "processed"
    assert r2.json()["status"] == "duplicate"
    assert await _count(session, UsageEvent, UsageEvent.kind == "checkout_completed") == 1


@pytest.mark.asyncio
async def test_checkout_completed_not_reemitted_on_plan_change(client, session, org):
    """A DISTINCT later event for the SAME subscription id (e.g. a plan
    change → `customer.subscription.updated`) must NOT re-emit
    `checkout_completed` — the `existing is None` guard fires only on the
    first time we mirror that sub id."""
    created = _evt("evt_A", "customer.subscription.created", {"id": "sub_PC"})
    updated = _evt("evt_B", "customer.subscription.updated", {"id": "sub_PC"})
    with patch("stripe.Subscription.retrieve", return_value=_sub_obj(sub_id="sub_PC")):
        assert (await _post_stripe(client, *_stripe_sign(created))).status_code == 200
    with patch(
        "stripe.Subscription.retrieve",
        return_value=_sub_obj(sub_id="sub_PC", price_id="price_TEST_team"),
    ):
        assert (await _post_stripe(client, *_stripe_sign(updated))).status_code == 200
    assert await _count(session, UsageEvent, UsageEvent.kind == "checkout_completed") == 1
