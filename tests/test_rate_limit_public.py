"""D4-harden Check 3a — per-IP rate limiting on the PUBLIC pre-auth
endpoints (/api/v1/health, /api/v1/version, /api/v1/billing/webhook).

Scope guard: this is the MINIMAL slice of `specs/rate-limit-abuse-v1.md`
(only the 3 Clerk-exempt endpoints). The full per-org token-bucket / daily
counter contract (spec §2) is the deferred D5+ Cycle AU work and is NOT
under test here. Clerk-gated routes are out of scope (already auth-gated).

slowapi storage is in-memory and process-global; `limiter.reset()` between
tests keeps the per-IP window isolated. Verified against slowapi==0.1.9.
"""

from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.rate_limit import limiter
from app.db.models import Base
from app.main import app


@pytest.fixture(autouse=True)
def _reset_limiter():
    """slowapi's in-memory window is process-global; clear it so each test
    starts from a full allowance and tests don't bleed into each other."""
    limiter.reset()
    yield
    limiter.reset()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_health_within_limit_ok(client: TestClient) -> None:
    """A handful of /health hits stay well under 60/min → all 200, and the
    X-RateLimit-* headers are present (headers_enabled=True)."""
    for _ in range(5):
        r = client.get("/api/v1/health")
        assert r.status_code == 200
    assert "X-RateLimit-Limit" in r.headers
    assert "X-RateLimit-Remaining" in r.headers


def test_health_over_limit_returns_429_with_retry_after(client: TestClient) -> None:
    """61 /health hits from one IP in the same minute: the 60-limit trips,
    the over-limit request is 429 with a Retry-After header and the house
    envelope (NOT slowapi's default {"error": ...} body)."""
    statuses = [client.get("/api/v1/health").status_code for _ in range(61)]
    assert statuses.count(200) == 60, f"expected 60x200, got {statuses.count(200)}"
    assert statuses[-1] == 429
    r = client.get("/api/v1/health")  # still over the window
    assert r.status_code == 429
    assert "Retry-After" in r.headers
    body = r.json()
    assert body["detail"]["code"] == "rate_limit_exceeded"
    assert "suggestion" in body["detail"]


def test_version_independently_rate_limited(client: TestClient) -> None:
    """/version has its own 60/min bucket; exhausting /health must NOT also
    429 /version (per-endpoint limit, slowapi keys by (ip, endpoint))."""
    for _ in range(61):
        client.get("/api/v1/health")
    assert client.get("/api/v1/health").status_code == 429
    assert client.get("/api/v1/version").status_code == 200


@pytest.fixture
def db_client(monkeypatch) -> TestClient:
    """The webhook resolves `Depends(get_session)` BEFORE the handler body,
    so it needs a wired engine even though the unsigned Check-1 400 path
    never queries. Mirror test_stripe_webhook_idempotency.client:115-137."""
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
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test_only_DO_NOT_USE_IN_PROD")

    import app.main as main_mod

    importlib.reload(main_mod)
    main_mod.app.state.limiter = limiter  # reuse the shared limiter we reset
    return TestClient(main_mod.app)


def test_webhook_has_higher_limit_for_stripe_retry_bursts(db_client: TestClient) -> None:
    """/billing/webhook is capped at 120/min (Stripe retry storms are
    legitimate, spec §2.3). Unsigned POSTs return the Check-1 400 envelope
    while UNDER the limit; the 121st trips the limiter → 429 + Retry-After."""
    statuses = [
        db_client.post("/api/v1/billing/webhook", content=b"{}").status_code for _ in range(120)
    ]
    assert all(s == 400 for s in statuses), "under-limit unsigned posts must be Check-1 400s"
    over = db_client.post("/api/v1/billing/webhook", content=b"{}")
    assert over.status_code == 429
    assert "Retry-After" in over.headers


def test_rate_limit_keyed_by_forwarded_ip_not_body(client: TestClient) -> None:
    """spec §1 'Authentication-path resolution' / R3 forge discipline: the
    limiter key is the X-Forwarded-For first hop, NEVER a body field. Two
    distinct client IPs get independent buckets even hammering the same
    endpoint; a forged body cannot share/escape another IP's bucket."""
    # IP-A exhausts its /health bucket.
    for _ in range(61):
        client.get("/api/v1/health", headers={"X-Forwarded-For": "203.0.113.1"})
    a = client.get("/api/v1/health", headers={"X-Forwarded-For": "203.0.113.1"})
    assert a.status_code == 429
    # IP-B (different first hop) is unaffected — independent bucket.
    b = client.get("/api/v1/health", headers={"X-Forwarded-For": "198.51.100.7"})
    assert b.status_code == 200
