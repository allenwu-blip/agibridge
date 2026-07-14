"""`POST /api/v1/events` — client-reported funnel ingestion (impl at
`app/api/events.py`).

Parity target: `tests/test_clerk_webhook_provisioning.py` /
`tests/test_rate_limit_public.py` — same in-memory sqlite posture
(`tests/conftest.py:99-101`), same ASGI client wiring, same shared-limiter
reset discipline (`test_rate_limit_public.py:26-32`).

Test → behavior mapping:

| Test                                                | Asserts                          |
|-----------------------------------------------------|----------------------------------|
| test_valid_event_writes_funnel_row_204              | allowlisted kind → 204 + 1 row   |
| test_clerk_exempt_no_jwt_required                   | no Authorization header → not 401|
| test_malformed_json_body_returns_400_not_500        | junk body → clean 400, no 500    |
| test_disallowed_kind_returns_400                    | conversion kind blocked → 400    |
| test_oversized_meta_returns_400                     | meta too big → 400, no row       |
| test_missing_anon_id_returns_400                    | anon_id required → 400           |
| test_no_pii_only_anon_id_and_meta_persisted         | only anon_id/kind/meta stored    |
| test_rate_limited_over_120_per_min                  | 121st from one IP → 429          |
"""

from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.rate_limit import limiter
from app.db.models import Base, FunnelEvent


@pytest.fixture(autouse=True)
def _reset_limiter():
    """slowapi's in-memory window is process-global (test_rate_limit_public
    .py:26-32); clear it so each test starts from a full allowance."""
    limiter.reset()
    yield
    limiter.reset()


@pytest.fixture
def db_client(monkeypatch):
    """ASGI TestClient bound to a fresh in-memory engine. Mirrors
    test_rate_limit_public.db_client:74-99. Returns (client, sessionmaker)."""
    import asyncio

    import app.db.session as db_session

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    async def _mk() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_mk())
    sm = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(db_session, "_engine", engine)
    monkeypatch.setattr(db_session, "_sessionmaker", sm)

    import app.main as main_mod

    importlib.reload(main_mod)
    main_mod.app.state.limiter = limiter
    return TestClient(main_mod.app), sm


async def _count(sm, model) -> int:
    async with sm() as s:
        return int((await s.execute(select(func.count()).select_from(model))).scalar_one() or 0)


def test_valid_event_writes_funnel_row_204(db_client) -> None:
    """An allowlisted client event → 204 No Content + exactly one
    `funnel_events` row with the anon_id/kind/meta as sent."""
    import asyncio

    client, sm = db_client
    r = client.post(
        "/api/v1/events",
        json={"kind": "landing_view", "anon_id": "anon-abc-123", "meta": {"ref": "tw"}},
    )
    assert r.status_code == 204
    assert r.content == b""
    assert asyncio.run(_count(sm, FunnelEvent)) == 1

    async def _row():
        async with sm() as s:
            return (await s.execute(select(FunnelEvent))).scalar_one()

    row = asyncio.run(_row())
    assert row.anon_id == "anon-abc-123"
    assert row.kind == "landing_view"
    assert row.meta == {"ref": "tw"}


def test_clerk_exempt_no_jwt_required(db_client) -> None:
    """The endpoint is in `clerk_auth._EXEMPT_EXACT`: a POST with NO
    Authorization header must NOT 401 (the whole top of the funnel fires
    pre-auth). 204 proves the Clerk middleware did not short-circuit."""
    client, _ = db_client
    r = client.post("/api/v1/events", json={"kind": "pricing_view", "anon_id": "a1"})
    assert r.status_code == 204


def test_malformed_json_body_returns_400_not_500(db_client) -> None:
    """A non-JSON body must be a CLEAN 400 (house envelope), never a 500
    traceback — instrumentation must not break the user flow."""
    client, sm = db_client
    import asyncio

    r = client.post(
        "/api/v1/events",
        content=b"not json at all {{{",
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 400
    body = r.json()
    assert body["detail"]["code"] == "invalid_event"
    assert "Traceback" not in r.text
    assert asyncio.run(_count(sm, FunnelEvent)) == 0


def test_disallowed_kind_returns_400(db_client) -> None:
    """A client must NOT be able to self-report a CONVERSION. The
    server-only kinds are absent from the allowlist → 400, no row."""
    client, sm = db_client
    import asyncio

    for forged in ("signup_completed", "checkout_completed", "totally_made_up"):
        r = client.post("/api/v1/events", json={"kind": forged, "anon_id": "a1"})
        assert r.status_code == 400, f"{forged} should be rejected"
        assert r.json()["detail"]["code"] == "invalid_event"
    assert asyncio.run(_count(sm, FunnelEvent)) == 0


def test_conversion_started_kind_is_allowlisted(db_client) -> None:
    """Track 3 funnel: `conversion_started` (signed-in user submitted the
    Dashboard upload form — first-conversion INTENT) is on the client
    allowlist → 204 + one `funnel_events` row. It is INTENT only: the
    conversion actually running is the server-side `job` lifecycle, same
    client-INTENT vs server-truth boundary as signup_started."""
    import asyncio

    client, sm = db_client
    r = client.post(
        "/api/v1/events",
        json={
            "kind": "conversion_started",
            "anon_id": "anon-conv-1",
            "meta": {"direction": "agibot__lerobot-v3"},
        },
    )
    assert r.status_code == 204
    assert asyncio.run(_count(sm, FunnelEvent)) == 1

    async def _row():
        async with sm() as s:
            return (await s.execute(select(FunnelEvent))).scalar_one()

    row = asyncio.run(_row())
    assert row.kind == "conversion_started"
    assert row.meta == {"direction": "agibot__lerobot-v3"}


def test_oversized_meta_returns_400(db_client) -> None:
    """An oversized `meta` (forge/flood vector on an FK-less append table)
    → 400, no row."""
    client, sm = db_client
    import asyncio

    r = client.post(
        "/api/v1/events",
        json={
            "kind": "landing_view",
            "anon_id": "a1",
            "meta": {"big": "x" * 5000},
        },
    )
    assert r.status_code == 400
    assert asyncio.run(_count(sm, FunnelEvent)) == 0


def test_missing_anon_id_returns_400(db_client) -> None:
    """`anon_id` is required (it is the only correlator) → 400 when absent."""
    client, sm = db_client
    import asyncio

    r = client.post("/api/v1/events", json={"kind": "landing_view"})
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "invalid_event"
    assert asyncio.run(_count(sm, FunnelEvent)) == 0


def test_no_pii_only_anon_id_and_meta_persisted(db_client) -> None:
    """Privacy: even if the client sends extra fields (an email, an ip),
    `extra='forbid'` rejects them → 400, nothing persisted. The schema
    cannot store PII because it has no column for it."""
    client, sm = db_client
    import asyncio

    r = client.post(
        "/api/v1/events",
        json={
            "kind": "landing_view",
            "anon_id": "a1",
            "email": "leak@example.com",
            "ip": "203.0.113.9",
        },
    )
    assert r.status_code == 400
    assert asyncio.run(_count(sm, FunnelEvent)) == 0
    # And the model itself has no PII columns.
    cols = {c.name for c in FunnelEvent.__table__.columns}
    assert cols == {"id", "anon_id", "kind", "meta", "occurred_at"}


def test_rate_limited_over_120_per_min(db_client) -> None:
    """120 events from one IP are accepted; the 121st in the same minute
    trips the per-IP limiter → 429 with Retry-After. Bad events must never
    500, and a flood must not be unbounded on an FK-less table."""
    client, _ = db_client
    statuses = [
        client.post(
            "/api/v1/events",
            json={"kind": "landing_view", "anon_id": "a1"},
            headers={"X-Forwarded-For": "203.0.113.50"},
        ).status_code
        for _ in range(120)
    ]
    assert statuses.count(204) == 120, f"expected 120x204, got {statuses.count(204)}"
    over = client.post(
        "/api/v1/events",
        json={"kind": "landing_view", "anon_id": "a1"},
        headers={"X-Forwarded-For": "203.0.113.50"},
    )
    assert over.status_code == 429
    assert "Retry-After" in over.headers
