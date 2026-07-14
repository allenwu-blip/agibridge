"""Clerk auto-personal-org webhook tests (impl at `app/api/webhooks.py`).

Parity target: `tests/test_stripe_webhook_idempotency.py` — same in-memory
sqlite posture (`tests/conftest.py:99-101`), same ASGI client wiring, same
real-signature-on-the-test-side discipline (we sign with the test webhook
secret EXACTLY as Svix would, via `svix.webhooks.Webhook.sign`, verified
round-tripping against svix==1.93.0) and the Clerk Backend API call is
mocked so no live Clerk request is made.

Test → behavior mapping:

| Test                                                       | Asserts                          |
|------------------------------------------------------------|----------------------------------|
| test_missing_svix_headers_returns_400                      | structural pre-check → 400       |
| test_malformed_svix_headers_returns_400                    | present-but-garbage → 400        |
| test_bad_signature_returns_403_no_db_write                 | well-formed bad HMAC → 403       |
| test_stale_timestamp_returns_403_replay                    | replay defense → 403             |
| test_valid_user_created_provisions_200                     | rows created + Clerk PATCH       |
| test_duplicate_delivery_is_idempotent_no_dup_rows          | 2nd delivery no error / no dups  |
| test_existing_public_metadata_org_id_skips_patch           | merge-not-clobber idempotent GET |
| test_existing_public_metadata_other_keys_preserved         | merge keeps prior keys           |
| test_non_user_created_returns_200_ignored                  | no-op ack (no retry storm)       |
| test_no_email_falls_back_to_workspace_name                 | NOT NULL email fallback          |
| test_clerk_api_failure_rolls_back_rows                     | metadata sync fail → 5xx + roll  |
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from svix.webhooks import Webhook

from app.db.models import Base, Org, OrgTier, User

WEBHOOK_SECRET = "whsec_dGVzdHNlY3JldGZvcnVuaXR0ZXN0c29ubHk="  # test-only
CLERK_SECRET_KEY = "sk_test_clerk_dummy"


# ----------------------- shared fixtures (mirror Stripe suite) -----------------------


@pytest.fixture(autouse=True)
def _clerk_env(monkeypatch):
    """Test-only values for the EXACT env names the handler reads
    (`app/api/billing_config.py` CLERK_* props). Prod values are HF secrets."""
    monkeypatch.setenv("CLERK_WEBHOOK_SECRET", WEBHOOK_SECRET)
    monkeypatch.setenv("CLERK_SECRET_KEY", CLERK_SECRET_KEY)


@pytest_asyncio.fixture
async def engine_sm():
    """In-memory sqlite engine + sessionmaker (Stripe-suite parity:24-28)."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    yield engine, sm
    await engine.dispose()


@pytest.fixture
def session(engine_sm):
    """Fresh-session FACTORY (aiosqlite single-connection snapshot caveat,
    test_stripe_webhook_idempotency.py:87-100)."""
    _engine, sm = engine_sm

    def _factory():
        return sm()

    return _factory


@pytest_asyncio.fixture
async def client(engine_sm, monkeypatch):
    """ASGI client bound to `app.main.app` with the in-memory engine patched
    into `app.db.session` (conftest.db_app:107-110 wiring)."""
    engine, sm = engine_sm
    import app.db.session as db_session

    monkeypatch.setattr(db_session, "_engine", engine)
    monkeypatch.setattr(db_session, "_sessionmaker", sm)

    import importlib

    import app.main as main_mod

    importlib.reload(main_mod)
    transport = ASGITransport(app=main_mod.app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        yield c


# ----------------------- signing helper (real Svix sig) -----------------------


def _sign(
    payload: dict, *, secret: str = WEBHOOK_SECRET, msg_id: str = "msg_test", t: int | None = None
):
    """Return (raw_bytes, svix headers) signed EXACTLY as Svix does, via the
    library's own `Webhook.sign` — verified round-tripping `verify(sign(...))`
    against svix==1.93.0 (`sign(msg_id, timestamp: datetime, data: str)`)."""
    raw = json.dumps(payload)
    ts = t if t is not None else int(time.time())
    sig = Webhook(secret).sign(msg_id, datetime.fromtimestamp(ts, UTC), raw)
    return raw.encode(), {
        "svix-id": msg_id,
        "svix-timestamp": str(ts),
        "svix-signature": sig,
    }


def _user_created_evt(user_id="user_NEW", email="ada@example.com", *, public_metadata=None):
    data: dict = {
        "id": user_id,
        "email_addresses": ([{"id": "idn_1", "email_address": email}] if email is not None else []),
        "primary_email_address_id": "idn_1" if email is not None else None,
    }
    if public_metadata is not None:
        data["public_metadata"] = public_metadata
    return {"type": "user.created", "object": "event", "data": data}


async def _post(client, raw, headers):
    return await client.post(
        "/api/v1/webhooks/clerk",
        content=raw,
        headers={**headers, "content-type": "application/json"},
    )


async def _count(session, model, where=None) -> int:
    async with session() as s:
        stmt = select(func.count()).select_from(model)
        if where is not None:
            stmt = stmt.where(where)
        return int((await s.execute(stmt)).scalar_one() or 0)


class _FakeClerkAPI:
    """Stand-in for the GET/PATCH Clerk Backend API calls. `get_meta` is the
    `public_metadata` the GET returns; `patched` captures the PATCH body so a
    test can assert the merge-not-clobber payload."""

    def __init__(self, get_meta=None):
        self._get_meta = get_meta if get_meta is not None else {}
        self.patched: dict | None = None
        self.get_called = False
        self.patch_called = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, headers=None):
        self.get_called = True
        # `request=` is required so `raise_for_status()` is callable on a
        # hand-built Response (httpx asserts the request instance is set).
        return httpx.Response(
            200,
            json={"id": "user_NEW", "public_metadata": self._get_meta},
            request=httpx.Request("GET", url),
        )

    async def patch(self, url, headers=None, json=None):
        self.patch_called = True
        self.patched = json
        return httpx.Response(
            200,
            json={"id": "user_NEW", "public_metadata": json["public_metadata"]},
            request=httpx.Request("PATCH", url),
        )


def _mock_clerk(fake):
    """Patch the AsyncClient constructor in app.api.webhooks to yield `fake`.
    No real Clerk request is made."""
    return patch("app.api.webhooks.httpx.AsyncClient", return_value=fake)


# ----------------------- signature taxonomy (mirror Stripe §3) -----------------------


@pytest.mark.asyncio
async def test_missing_svix_headers_returns_400(client, session):
    """Structural pre-check: no svix-* headers → 400 `invalid_request`
    (clean envelope, no internal detail), no DB write. Mirrors
    test_stripe_webhook_idempotency.py:test_missing_signature_header."""
    raw = json.dumps(_user_created_evt()).encode()
    r = await client.post(
        "/api/v1/webhooks/clerk",
        content=raw,
        headers={"content-type": "application/json"},
    )
    assert r.status_code == 400
    body = r.json()
    assert body["code"] == "invalid_request"
    assert body["message"] == "Missing or malformed Svix signature headers."
    assert "Traceback" not in json.dumps(body)
    assert await _count(session, Org) == 0
    assert await _count(session, User) == 0


@pytest.mark.asyncio
async def test_malformed_svix_headers_returns_400(client, session):
    """Present-but-structurally-invalid svix headers → 400, NOT 403, NOT
    500. Mirrors test_stripe_webhook_idempotency.py:test_malformed_signature."""
    raw = json.dumps(_user_created_evt()).encode()
    bad_sets = [
        {"svix-id": "m", "svix-timestamp": "notanumber", "svix-signature": "v1,abc"},
        {"svix-id": "m", "svix-timestamp": str(int(time.time())), "svix-signature": "garbage"},
        {"svix-id": "", "svix-timestamp": str(int(time.time())), "svix-signature": "v1,abc"},
        {"svix-id": "m", "svix-timestamp": str(int(time.time())), "svix-signature": ","},
    ]
    for hdrs in bad_sets:
        r = await _post(client, raw, hdrs)
        assert r.status_code == 400, f"{hdrs} did not 400"
        assert r.json()["code"] == "invalid_request"
    assert await _count(session, Org) == 0


@pytest.mark.asyncio
async def test_bad_signature_returns_403_no_db_write(client, session):
    """Well-formed triple whose v1 HMAC does not verify → 403
    `signature_verification_failed`, no rows, no internal detail leak.
    Mirrors test_stripe_webhook_idempotency.py:test_bad_signature_403."""
    raw, hdrs = _sign(_user_created_evt())
    hdrs["svix-signature"] = "v1,ZGVhZGJlZWZkZWFkYmVlZmRlYWRiZWVm"  # valid shape, wrong sig
    r = await _post(client, raw, hdrs)
    assert r.status_code == 403
    body = r.json()
    assert body["code"] == "signature_verification_failed"
    assert body["message"] == "Svix signature verification failed."
    assert "Traceback" not in json.dumps(body)
    assert await _count(session, Org) == 0
    assert await _count(session, User) == 0


@pytest.mark.asyncio
async def test_stale_timestamp_returns_403_replay(client, session):
    """Valid HMAC but timestamp far in the past → Svix rejects as replay →
    well-formed header so 403 (not 400). Mirrors
    test_stripe_webhook_idempotency.py:test_timestamp_outside_tolerance."""
    raw, hdrs = _sign(_user_created_evt(), t=int(time.time()) - 60 * 60 * 24)
    r = await _post(client, raw, hdrs)
    assert r.status_code == 403
    assert r.json()["code"] == "signature_verification_failed"
    assert await _count(session, Org) == 0


# ----------------------- provisioning + idempotency -----------------------


@pytest.mark.asyncio
async def test_valid_user_created_provisions_200(client, session):
    """Valid `user.created` → 200 `provisioned`; orgs+users rows created with
    the deterministic id; Clerk PATCH issued with org_id (Clerk API mocked)."""
    fake = _FakeClerkAPI(get_meta={})
    ev = _user_created_evt("user_ABC", "grace@example.com")
    with _mock_clerk(fake):
        r = await _post(client, *_sign(ev))
    assert r.status_code == 200
    j = r.json()
    assert j["status"] == "provisioned"
    assert j["org_id"] == "org_personal_user_ABC"

    async with session() as s:
        org = (await s.execute(select(Org).where(Org.id == "org_personal_user_ABC"))).scalar_one()
        assert org.name == "grace@example.com"
        assert org.tier == OrgTier.free
        user = (await s.execute(select(User).where(User.id == "user_ABC"))).scalar_one()
        assert user.email == "grace@example.com"
        assert user.org_id == "org_personal_user_ABC"

    assert fake.get_called and fake.patch_called
    assert fake.patched == {"public_metadata": {"org_id": "org_personal_user_ABC"}}


@pytest.mark.asyncio
async def test_duplicate_delivery_is_idempotent_no_dup_rows(client, session):
    """A duplicate Svix delivery (retry / dashboard resend): second call
    succeeds with no error and creates NO duplicate rows. Analogue of the
    Stripe duplicate-event idempotency test."""
    ev = _user_created_evt("user_DUP", "dup@example.com")
    fake1 = _FakeClerkAPI(get_meta={})
    with _mock_clerk(fake1):
        r1 = await _post(client, *_sign(ev))
    assert r1.status_code == 200

    # Second delivery: Clerk now reports org_id already set (the prior PATCH
    # landed), so the merge-not-clobber path skips the re-PATCH.
    fake2 = _FakeClerkAPI(get_meta={"org_id": "org_personal_user_DUP"})
    with _mock_clerk(fake2):
        r2 = await _post(client, *_sign(ev, msg_id="msg_test2"))
    assert r2.status_code == 200
    assert r2.json()["status"] == "provisioned"

    assert await _count(session, Org, Org.id == "org_personal_user_DUP") == 1
    assert await _count(session, User, User.id == "user_DUP") == 1
    assert fake2.get_called and not fake2.patch_called  # idempotent skip


@pytest.mark.asyncio
async def test_existing_public_metadata_org_id_skips_patch(client, session):
    """Clerk already has `public_metadata.org_id` → GET sees it, PATCH is
    skipped entirely (idempotent + does not re-clobber)."""
    fake = _FakeClerkAPI(get_meta={"org_id": "org_personal_user_PRE"})
    ev = _user_created_evt("user_PRE", "pre@example.com")
    with _mock_clerk(fake):
        r = await _post(client, *_sign(ev))
    assert r.status_code == 200
    assert fake.get_called
    assert fake.patch_called is False  # already set → no PATCH


@pytest.mark.asyncio
async def test_existing_public_metadata_other_keys_preserved(client, session):
    """Merge-not-clobber: Clerk has OTHER public_metadata keys but no
    org_id → PATCH body merges org_id in while preserving the prior keys
    (the full-replace endpoint must not drop them)."""
    fake = _FakeClerkAPI(get_meta={"plan": "beta", "ref": "campaign42"})
    ev = _user_created_evt("user_MERGE", "merge@example.com")
    with _mock_clerk(fake):
        r = await _post(client, *_sign(ev))
    assert r.status_code == 200
    assert fake.patched == {
        "public_metadata": {
            "plan": "beta",
            "ref": "campaign42",
            "org_id": "org_personal_user_MERGE",
        }
    }


@pytest.mark.asyncio
async def test_non_user_created_returns_200_ignored(client, session):
    """Any other event type → 200 `ignored`, no rows, no Clerk call (so
    Clerk does NOT retry-storm; analogue of Stripe §6 fallthrough)."""
    fake = _FakeClerkAPI()
    ev = {"type": "user.updated", "object": "event", "data": {"id": "user_X"}}
    with _mock_clerk(fake):
        r = await _post(client, *_sign(ev))
    assert r.status_code == 200
    assert r.json() == {"status": "ignored"}
    assert await _count(session, Org) == 0
    assert await _count(session, User) == 0
    assert fake.get_called is False and fake.patch_called is False


@pytest.mark.asyncio
async def test_no_email_falls_back_to_workspace_name(client, session):
    """A user.created with no email address still provisions: org name falls
    back to `<user>'s workspace` and users.email (NOT NULL,
    models.py:150) falls back to the user id."""
    fake = _FakeClerkAPI(get_meta={})
    ev = _user_created_evt("user_NOEMAIL", email=None)
    with _mock_clerk(fake):
        r = await _post(client, *_sign(ev))
    assert r.status_code == 200
    async with session() as s:
        org = (
            await s.execute(select(Org).where(Org.id == "org_personal_user_NOEMAIL"))
        ).scalar_one()
        assert org.name == "user_NOEMAIL's workspace"
        user = (await s.execute(select(User).where(User.id == "user_NOEMAIL"))).scalar_one()
        assert user.email == "user_NOEMAIL"  # non-null fallback


@pytest.mark.asyncio
async def test_clerk_api_failure_rolls_back_rows(client, session):
    """Clerk Backend API failure RAISES → get_session rolls back the org/user
    rows → 5xx so Clerk retries cleanly (no half-provisioned state). Analogue
    of test_stripe_webhook_idempotency.py:test_subscription_retrieve_api_failure."""
    failing = AsyncMock()
    failing.__aenter__ = AsyncMock(return_value=failing)
    failing.__aexit__ = AsyncMock(return_value=False)
    failing.get = AsyncMock(side_effect=httpx.ConnectError("simulated Clerk 5xx"))

    ev = _user_created_evt("user_FAIL", "fail@example.com")
    with patch("app.api.webhooks.httpx.AsyncClient", return_value=failing):
        r = await _post(client, *_sign(ev))
    assert r.status_code >= 500  # propagated → Clerk will retry
    # rows rolled back atomically with the failed metadata sync
    assert await _count(session, Org, Org.id == "org_personal_user_FAIL") == 0
    assert await _count(session, User, User.id == "user_FAIL") == 0
