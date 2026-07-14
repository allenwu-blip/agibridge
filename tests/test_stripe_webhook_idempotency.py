"""Stripe webhook idempotency + race-condition tests (D4-B impl of Cycle G).

DESIGN SPEC: `app/api/stripe_webhook_spec.md` (handler at `app/api/billing.py`).

Test → spec mapping (spec §7 verification matrix + Cycle N extensions):

| Test name                                                       | Spec §       |
|-----------------------------------------------------------------|--------------|
| test_duplicate_event_id_returns_200_no_mutation                 | §1, §2.3     |
| test_out_of_order_updated_before_created_uses_api_truth         | §2.1, §4.1   |
| test_deleted_before_final_invoice_paid_uses_subscription_status | §2.2         |
| test_committed_then_crashed_retry_is_safe_idempotent            | §1, §2.4     |
| test_dashboard_resend_old_event_is_duplicate                    | §2.5         |
| test_missing_signature_header_returns_400                       | §3.2, D4-h1  |
| test_malformed_signature_header_returns_400                     | D4-harden c1 |
| test_bad_signature_returns_403_no_db_write                      | §3.2, D4-h1  |
| test_timestamp_outside_tolerance_returns_403_replay             | §3.3, D4-h1  |
| test_malformed_json_body_with_valid_signature_returns_400       | D4-harden c1 |
| test_valid_signed_event_returns_200_processed                   | D4-harden c1 |
| test_subscription_retrieve_api_failure_rolls_back_event_row     | §6, §4.1     |
| test_concurrent_duplicate_delivery_only_one_succeeds            | §2.3         |
| test_unknown_event_type_inserts_audit_row_no_mutation           | §6 fallthru  |
| test_invoice_payment_failed_records_event_no_immediate_downgrade| §0, §6       |
| test_invoice_paid_during_r2_outage_succeeds_independent         | §6 no-R2-dep |

Test infra parity with `tests/test_r3_cross_org_isolation.py:13-17` —
sqlite+aiosqlite in-memory. The race logic under test is ORM-portable;
true-parallel Postgres test deferred to D5+ (spec §2.3 final paragraph).

Stripe SDK note: `stripe.Webhook.construct_event` is used on the TEST side
to GENERATE valid signatures (we sign with the test webhook secret exactly
as Stripe would), and `unittest.mock.patch` stubs
`stripe.Subscription.retrieve` / `stripe.Invoice.retrieve` so no live API
call is made. Verified empirically against `stripe==8.11.0`:
`construct_event(payload, sig_header, secret, tolerance=300)` raises
`stripe.SignatureVerificationError` on bad HMAC OR stale timestamp.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.models import Base, Org, OrgTier, StripeEvent, Subscription

WEBHOOK_SECRET = "whsec_test_only_DO_NOT_USE_IN_PROD"
SOLO_PRICE = "price_TEST_solo"
TEAM_PRICE = "price_TEST_team"

# ----------------------- shared fixtures -----------------------


@pytest.fixture(autouse=True)
def _stripe_env(monkeypatch):
    """The 6 EXACT env names (D4-B brief ground truth). Tests set the
    test-only values; prod values are HF Space / GH secrets."""
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_dummy")
    monkeypatch.setenv("STRIPE_PUBLISHABLE_KEY", "pk_test_dummy")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", WEBHOOK_SECRET)
    monkeypatch.setenv("STRIPE_PRICE_SOLO", SOLO_PRICE)
    monkeypatch.setenv("STRIPE_PRICE_TEAM", TEAM_PRICE)
    monkeypatch.setenv("STRIPE_PRICE_ENTERPRISE", "price_TEST_enterprise")


@pytest_asyncio.fixture
async def engine_sm():
    """In-memory sqlite engine + sessionmaker. Mirrors
    test_r3_cross_org_isolation.py:35-43."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    yield engine, sm
    await engine.dispose()


@pytest.fixture
def session(engine_sm):
    """Fresh-session FACTORY, not a long-lived session.

    aiosqlite serializes all work onto ONE shared connection; a session held
    open across an HTTP request (which commits on its own session) sees a
    stale snapshot until its own tx ends. Every read/seed therefore opens a
    short-lived session via `async with session() as s`."""
    _engine, sm = engine_sm

    def _factory():
        return sm()

    return _factory


@pytest_asyncio.fixture
async def org(session):
    """Single Org, stripe_customer_id set, tier starts `free`."""
    async with session() as s:
        o = Org(id="org_TEST", name="Test Labs", stripe_customer_id="cus_TEST123")
        s.add(o)
        await s.commit()
    return o


@pytest.fixture
def webhook_secret():
    return WEBHOOK_SECRET


@pytest_asyncio.fixture
async def client(engine_sm, monkeypatch):
    """ASGI client bound to `app.main.app` with the in-memory engine patched
    into `app.db.session`. Mirrors conftest.db_app:107-110 wiring so the
    request-scoped `get_session()` shares the test engine."""
    engine, sm = engine_sm
    import app.db.session as db_session

    monkeypatch.setattr(db_session, "_engine", engine)
    monkeypatch.setattr(db_session, "_sessionmaker", sm)

    import importlib

    import app.main as main_mod

    importlib.reload(main_mod)
    # raise_app_exceptions=False: an unhandled handler exception (e.g. the
    # §6 Subscription.retrieve-fails path) becomes a real 500 response —
    # exactly what Stripe would see and retry on — instead of bubbling into
    # the test as a raw traceback.
    transport = ASGITransport(app=main_mod.app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        yield c


# ----------------------- signing helper -----------------------


def _sign(payload: dict, *, secret: str = WEBHOOK_SECRET, t: int | None = None):
    """Produce `(raw_bytes, Stripe-Signature header)` exactly as Stripe does:
    v1 = HMAC-SHA256(secret, f"{t}.{raw_body}"). Verified against
    stripe==8.11.0 construct_event roundtrip."""
    raw = json.dumps(payload).encode()
    ts = t if t is not None else int(time.time())
    sig = hmac.new(secret.encode(), f"{ts}.".encode() + raw, hashlib.sha256).hexdigest()
    return raw, f"t={ts},v1={sig}"


def _sub_obj(sub_id="sub_X", *, status="active", price_id=SOLO_PRICE, customer="cus_TEST123"):
    """A minimal Stripe Subscription-shaped dict (what
    `stripe.Subscription.retrieve` would return). The handler reads
    items.data[0].price.id, status, current_period_end, customer, id."""
    return {
        "id": sub_id,
        "status": status,
        "customer": customer,
        "current_period_end": int(time.time()) + 30 * 86400,
        "items": {"data": [{"price": {"id": price_id}}]},
    }


def _evt(event_id, event_type, obj):
    return {"id": event_id, "type": event_type, "data": {"object": obj}}


async def _post(client, raw, sig_header):
    return await client.post(
        "/api/v1/billing/webhook",
        content=raw,
        headers={"stripe-signature": sig_header, "content-type": "application/json"},
    )


async def _org_tier(session) -> OrgTier:
    async with session() as s:
        return (await s.execute(select(Org).where(Org.id == "org_TEST"))).scalar_one().tier


async def _count(session, model, where=None) -> int:
    async with session() as s:
        stmt = select(func.count()).select_from(model)
        if where is not None:
            stmt = stmt.where(where)
        return int((await s.execute(stmt)).scalar_one() or 0)


# ----------------------- idempotency + race scenarios (§2) -----------------------


@pytest.mark.asyncio
async def test_duplicate_event_id_returns_200_no_mutation(client, session, org):
    """Spec §1 + §2.3 retry storm: same evt_ABC twice → 2nd is duplicate,
    no 2nd Subscription.retrieve, tier unchanged after first apply."""
    ev = _evt("evt_ABC123", "customer.subscription.created", {"id": "sub_X"})

    with patch("stripe.Subscription.retrieve", return_value=_sub_obj()) as m:
        r1 = await _post(client, *_sign(ev))
        assert r1.status_code == 200
        assert r1.json()["status"] == "processed"
        r2 = await _post(client, *_sign(ev))  # retry storm, same event id
        assert r2.status_code == 200
        assert r2.json()["status"] == "duplicate"
        assert r2.json()["event_id"] == "evt_ABC123"
        assert m.call_count == 1  # duplicate short-circuits before §6.6 re-fetch

    assert await _org_tier(session) == OrgTier.solo  # set once, not re-applied
    assert await _count(session, StripeEvent, StripeEvent.id == "evt_ABC123") == 1


@pytest.mark.asyncio
async def test_out_of_order_updated_before_created_uses_api_truth(client, session, org):
    """Spec §2.1 + §4.1: `updated`(payload=team) arrives BEFORE
    `created`(payload=solo). API re-fetch returns team for BOTH → final
    tier=team regardless of order; payload contents NOT used."""
    updated = _evt("evt_UPD", "customer.subscription.updated", {"id": "sub_X"})
    created = _evt("evt_CRE", "customer.subscription.created", {"id": "sub_X"})

    with patch("stripe.Subscription.retrieve", return_value=_sub_obj(price_id=TEAM_PRICE)):
        ru = await _post(client, *_sign(updated))
        rc = await _post(client, *_sign(created))

    assert ru.status_code == 200 and rc.status_code == 200
    assert await _org_tier(session) == OrgTier.team  # converged to API truth


@pytest.mark.asyncio
async def test_deleted_before_final_invoice_paid_uses_subscription_status_truth(
    client, session, org
):
    """Spec §2.2: `deleted`(status=canceled) then `invoice.paid` for the
    canceled sub. After deleted → tier=free but current_period_end PRESERVED
    (§4.3). invoice.paid for a canceled sub → no tier resurrection."""
    created = _evt("evt_C", "customer.subscription.created", {"id": "sub_X"})
    with patch("stripe.Subscription.retrieve", return_value=_sub_obj()):
        assert (await _post(client, *_sign(created))).status_code == 200

    deleted = _evt("evt_D", "customer.subscription.deleted", {"id": "sub_X"})
    canceled_sub = _sub_obj(status="canceled")
    canceled_sub["current_period_end"] = int(time.time()) + 10 * 86400
    with patch("stripe.Subscription.retrieve", return_value=canceled_sub):
        assert (await _post(client, *_sign(deleted))).status_code == 200

    assert await _org_tier(session) == OrgTier.free  # downgraded by subscription.status
    async with session() as s:
        sub_row = (
            await s.execute(select(Subscription).where(Subscription.id == "sub_X"))
        ).scalar_one()
        assert sub_row.status == "canceled"
        cpe = sub_row.current_period_end
        if cpe.tzinfo is None:  # sqlite stores DateTime tz-naive
            cpe = cpe.replace(tzinfo=UTC)
        # period preserved so tier-gating still grants access until then (§4.3)
        assert cpe > datetime.now(UTC) + timedelta(days=5)

    inv = _evt("evt_I", "invoice.paid", {"id": "in_X"})
    inv_obj = {"id": "in_X", "customer": "cus_TEST123", "amount_paid": 5000, "currency": "usd"}
    with patch("stripe.Invoice.retrieve", return_value=inv_obj):
        assert (await _post(client, *_sign(inv))).status_code == 200
    assert await _org_tier(session) == OrgTier.free  # no resurrection


@pytest.mark.asyncio
async def test_committed_then_crashed_retry_is_safe_idempotent(client, session, org):
    """Spec §2.4: row committed (handler crashed before 200), Stripe retries
    → PK collision → 200 duplicate, no re-fetch, no 2nd mutation."""
    async with session() as s:
        s.add(
            StripeEvent(
                id="evt_CRASH",
                type="customer.subscription.created",
                payload={"id": "evt_CRASH"},
                processed_at=datetime.now(UTC),
            )
        )
        o = (await s.execute(select(Org).where(Org.id == "org_TEST"))).scalar_one()
        o.tier = OrgTier.solo
        await s.commit()

    ev = _evt("evt_CRASH", "customer.subscription.created", {"id": "sub_X"})
    with patch("stripe.Subscription.retrieve") as m:
        r = await _post(client, *_sign(ev))
        assert r.status_code == 200
        assert r.json()["status"] == "duplicate"
        m.assert_not_called()  # no re-fetch on the duplicate path

    assert await _org_tier(session) == OrgTier.solo  # unchanged


@pytest.mark.asyncio
async def test_dashboard_resend_old_event_is_duplicate(client, session, org):
    """Spec §2.5: dashboard Resend reuses the original event.id → same path
    as §2.3 retry storm."""
    ev = _evt("evt_RESEND", "customer.subscription.updated", {"id": "sub_X"})
    with patch("stripe.Subscription.retrieve", return_value=_sub_obj()):
        first = await _post(client, *_sign(ev))
        assert first.status_code == 200 and first.json()["status"] == "processed"
        resend = await _post(client, *_sign(ev, t=int(time.time())))
    assert resend.status_code == 200
    assert resend.json()["status"] == "duplicate"


# ----------------------- signature + replay (§3) -----------------------


@pytest.mark.asyncio
async def test_missing_signature_header_returns_400(client, session, org):
    """Spec §3.2 + D4-harden Check 1: no Stripe-Signature header → 400
    `invalid_request` (clean envelope, no internal detail), no DB write."""
    raw, _ = _sign(_evt("evt_NOSIG", "customer.subscription.created", {"id": "sub_X"}))
    r = await client.post(
        "/api/v1/billing/webhook",
        content=raw,
        headers={"content-type": "application/json"},
    )
    assert r.status_code == 400
    body = r.json()
    assert body["code"] == "invalid_request"
    assert body["message"] == "Missing or malformed Stripe-Signature header."
    assert await _count(session, StripeEvent) == 0


@pytest.mark.asyncio
async def test_malformed_signature_header_returns_400(client, session, org):
    """D4-harden Check 1: a present-but-structurally-invalid Stripe-Signature
    header (no `t=`/`v1=` tokens) is malformed INPUT → 400 `invalid_request`,
    NOT a 403 verification failure and NOT a 500. No DB write."""
    raw = json.dumps(_evt("evt_GARBAGE", "customer.subscription.created", {"id": "sub_X"})).encode()
    for bad_header in ("garbage", "t=,v1=", "v1=deadbeef", "t=notanumber,v1=abc"):
        r = await _post(client, raw, bad_header)
        assert r.status_code == 400, f"header {bad_header!r} did not 400"
        assert r.json()["code"] == "invalid_request"
    assert await _count(session, StripeEvent) == 0


@pytest.mark.asyncio
async def test_bad_signature_returns_403_no_db_write(client, session, org):
    """Spec §3.2 + D4-harden Check 1: a WELL-FORMED header whose v1 HMAC does
    not verify is a genuine signature failure → 403
    `signature_verification_failed` (was 400 pre-harden), no stripe_events
    row, no internal exception detail in the body."""
    raw = json.dumps(_evt("evt_BAD", "customer.subscription.created", {"id": "sub_X"})).encode()
    bad_header = f"t={int(time.time())},v1=deadbeefdeadbeef"
    r = await _post(client, raw, bad_header)
    assert r.status_code == 403
    body = r.json()
    assert body["code"] == "signature_verification_failed"
    assert body["message"] == "Stripe signature verification failed."
    # no traceback / internal detail leaked
    assert "Traceback" not in json.dumps(body)
    assert await _count(session, StripeEvent) == 0


@pytest.mark.asyncio
async def test_timestamp_outside_tolerance_returns_403_replay(client, session, org):
    """Spec §3.3 + §3.4 + D4-harden Check 1: valid v1 HMAC but t = now-600s
    (> 300s tolerance) → SignatureVerificationError → 403 (replay defense;
    was 400 pre-harden). Layer-1 fires before the PK layer. No DB write."""
    ev = _evt("evt_REPLAY", "customer.subscription.created", {"id": "sub_X"})
    raw, sig = _sign(ev, t=int(time.time()) - 600)  # 10 min old, tolerance 300s
    r = await _post(client, raw, sig)
    assert r.status_code == 403
    assert r.json()["code"] == "signature_verification_failed"
    assert await _count(session, StripeEvent) == 0


@pytest.mark.asyncio
async def test_malformed_json_body_with_valid_signature_returns_400(client, session, org):
    """D4-harden Check 1 (the real pre-harden 500): a CORRECTLY SIGNED body
    that is not valid JSON. construct_event passes verify_header then raises
    json.JSONDecodeError (⊂ ValueError) — UNCAUGHT pre-harden → 500. Now a
    clean 400 `invalid_request`, no DB write, no traceback leak."""
    bad_json = b"{not valid json at all"
    ts = int(time.time())
    sig = hmac.new(
        WEBHOOK_SECRET.encode(), f"{ts}.".encode() + bad_json, hashlib.sha256
    ).hexdigest()
    r = await _post(client, bad_json, f"t={ts},v1={sig}")
    assert r.status_code == 400
    body = r.json()
    assert body["code"] == "invalid_request"
    assert body["message"] == "Malformed webhook payload."
    assert "Traceback" not in json.dumps(body)
    assert await _count(session, StripeEvent) == 0


@pytest.mark.asyncio
async def test_valid_signed_event_returns_200_processed(client, session, org):
    """D4-harden Check 1 positive control: a properly signed, well-formed
    event still flows through to 200 `processed` (the harden changes ONLY
    the rejection paths, not the happy path)."""
    ev = _evt("evt_OK_HARDEN", "customer.subscription.created", {"id": "sub_X"})
    with patch("stripe.Subscription.retrieve", return_value=_sub_obj()):
        r = await _post(client, *_sign(ev))
    assert r.status_code == 200
    assert r.json()["status"] == "processed"


# ----------------------- Cycle N · §2.x extensions -----------------------


@pytest.mark.asyncio
async def test_subscription_retrieve_api_failure_rolls_back_event_row(client, session, org):
    """Cycle N / spec §6 'step 5 INSIDE the tx': INSERT succeeds, then
    Subscription.retrieve raises → ENTIRE tx rolls back incl. the
    stripe_events row → 5xx → Stripe retries cleanly (no orphan 'seen'
    row)."""
    import stripe

    ev = _evt("evt_APIFAIL", "customer.subscription.created", {"id": "sub_X"})
    with patch(
        "stripe.Subscription.retrieve",
        side_effect=stripe.error.APIConnectionError("simulated Stripe 5xx"),
    ):
        r = await _post(client, *_sign(ev))
    assert r.status_code >= 500  # propagated → Stripe will retry
    # event row rolled back WITH the failed state mutation (no permanent drift)
    assert await _count(session, StripeEvent, StripeEvent.id == "evt_APIFAIL") == 0
    assert await _org_tier(session) == OrgTier.free  # untouched


@pytest.mark.asyncio
async def test_concurrent_duplicate_delivery_only_one_succeeds(client, session, org):
    """Cycle N / spec §2.3: sequential proxy for the deferred-to-D5+ Neon
    concurrent test. Two deliveries of the same event id: exactly one
    'processed', the other 'duplicate', one side-effect set."""
    ev = _evt("evt_CONC", "customer.subscription.created", {"id": "sub_X"})
    with patch("stripe.Subscription.retrieve", return_value=_sub_obj()):
        a = await _post(client, *_sign(ev))
        b = await _post(client, *_sign(ev))
    assert sorted([a.json()["status"], b.json()["status"]]) == ["duplicate", "processed"]
    assert await _count(session, StripeEvent, StripeEvent.id == "evt_CONC") == 1


@pytest.mark.asyncio
async def test_unknown_event_type_inserts_audit_row_no_mutation(client, session, org):
    """Cycle N / spec §6 fallthrough: an out-of-scope event type
    (customer.discount.created) → audit row inserted, processed_at SET (so
    the stuck-handler tripwire never fires), tier UNCHANGED, 200."""
    ev = _evt("evt_DISC", "customer.discount.created", {"id": "di_X"})
    r = await _post(client, *_sign(ev))
    assert r.status_code == 200
    assert r.json()["status"] == "processed"
    async with session() as s:
        row = (
            await s.execute(select(StripeEvent).where(StripeEvent.id == "evt_DISC"))
        ).scalar_one()
        assert row.processed_at is not None  # tripwire-safe
    assert await _org_tier(session) == OrgTier.free
    assert await _count(session, Subscription) == 0


@pytest.mark.asyncio
async def test_invoice_payment_failed_records_event_no_immediate_downgrade(client, session, org):
    """Cycle N / spec §0 + DR-008 dunning: invoice.payment_failed for a
    solo org → audit row, tier STAYS solo (downgrade waits for Stripe's
    subscription.deleted/updated), 200."""
    created = _evt("evt_SUB", "customer.subscription.created", {"id": "sub_X"})
    with patch("stripe.Subscription.retrieve", return_value=_sub_obj()):
        await _post(client, *_sign(created))

    ev = _evt("evt_FAIL", "invoice.payment_failed", {"id": "in_F"})
    inv_obj = {"id": "in_F", "customer": "cus_TEST123", "attempt_count": 2}
    with patch("stripe.Invoice.retrieve", return_value=inv_obj):
        r = await _post(client, *_sign(ev))
    assert r.status_code == 200
    assert await _org_tier(session) == OrgTier.solo  # NOT downgraded by payment_failed
    async with session() as s:
        ev_row = (
            await s.execute(select(StripeEvent).where(StripeEvent.id == "evt_FAIL"))
        ).scalar_one()
        assert ev_row.processed_at is not None


@pytest.mark.asyncio
async def test_invoice_paid_during_r2_outage_succeeds_independent(client, session, org):
    """Cycle N: webhook MUST NOT depend on R2. With R2 client patched to
    raise on ANY call, invoice.paid still 200s and R2 is never invoked."""
    from app.storage import r2

    sentinel = {"called": False}

    def _boom(*a, **k):
        sentinel["called"] = True
        raise RuntimeError("R2 503 — must not be reached by the webhook path")

    with patch.object(r2, "_client", _boom):
        ev = _evt("evt_R2", "invoice.paid", {"id": "in_R2"})
        inv_obj = {
            "id": "in_R2",
            "customer": "cus_TEST123",
            "amount_paid": 5000,
            "currency": "usd",
        }
        with patch("stripe.Invoice.retrieve", return_value=inv_obj):
            r = await _post(client, *_sign(ev))
    assert r.status_code == 200
    assert sentinel["called"] is False  # webhook never touched R2
