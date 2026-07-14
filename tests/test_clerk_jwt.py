"""Clerk JWT middleware verification — D4-A Story #2 implementation.

Implements the 6 Cycle-N stubs. The middleware lands at
`app/api/clerk_auth.py::ClerkJWTMiddleware`; this suite mints RS256 JWTs
with a local RSA keypair and injects a fake `jwks_client` (PyJWT's
`PyJWKClient` substitute) so verification is network-pure.

Status taxonomy under test (pinned here = the contract D4 satisfies):
- 401: missing/malformed Authorization, bad signature, expired, wrong
  issuer (`clerk_auth.py` `_AuthError(401, ...)`).
- 403: cryptographically valid + issuer OK, but NO active-org claim
  (`clerk_auth.py` no_organization). Source-grounded: stub
  `test_missing_org_id_claim_returns_403_authn_ok_authz_fail` +
  `app/db/models.py:141-148` (User.org_id nullable at DB, required at
  app layer). The middleware does NOT synthesize a `personal:<uid>` org.

Clerk claim shapes accepted (https://clerk.com/docs/backend-requests/
resources/session-tokens, accessed 2026-05-16): v2 nested `o.id` and v1 /
custom-template flat `org_id`. Both covered below.

Anthropic-over-OpenAI: no LLM dependency; pure RS256 crypto (PyJWT).
"""

from __future__ import annotations

import time
import uuid

import jwt
import pytest
import pytest_asyncio
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI, Request
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.testclient import TestClient

from app.api.clerk_auth import ClerkJWTMiddleware
from app.db.models import Base

# ----------------------- shared fixtures -----------------------

_TEST_ISSUER = "https://relevant-mosquito-93.clerk.accounts.dev"
_TEST_KID = "test-key-1"


@pytest_asyncio.fixture
async def session():
    """In-memory sqlite. Mirrors test_r3_cross_org_isolation.py:34-42."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as s:
        yield s
    await engine.dispose()


class _FakeSigningKey:
    """Mimics PyJWT's `PyJWK`: exposes `.key` (the loaded public key)."""

    def __init__(self, key) -> None:
        self.key = key


class _FakeJWKSClient:
    """Substitute for `jwt.PyJWKClient`. Returns the test public key for any
    token (no network). The middleware only calls
    `get_signing_key_from_jwt(token)`."""

    def __init__(self, public_key) -> None:
        self._public_key = public_key

    def get_signing_key_from_jwt(self, token: str) -> _FakeSigningKey:
        return _FakeSigningKey(self._public_key)


@pytest.fixture
def test_keypair():
    """RSA keypair for signing test JWTs.

    Returns (private_key_pem: bytes, public_key_obj) — the middleware's
    JWKS fetcher is mocked via `_FakeJWKSClient(public_key_obj)`.
    """
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return private_pem, private_key.public_key()


@pytest.fixture
def issuer_url():
    """Clerk's `iss` claim — the D4-A instance from the publishable key."""
    return _TEST_ISSUER


def _mint(
    private_pem: bytes,
    *,
    issuer: str,
    sub: str = "user_TEST",
    org_id: str | None = "org_TEST_VALID",
    org_style: str = "flat",
    exp_delta: int = 60,
) -> str:
    """Sign an RS256 JWT. `org_style`: 'flat' → top-level org_id (v1 /
    custom template); 'nested' → Clerk v2 `o.id`; org_id None → omit."""
    now = int(time.time())
    claims: dict = {
        "sub": sub,
        "iss": issuer,
        "iat": now,
        "exp": now + exp_delta,
        "azp": issuer,
    }
    if org_id is not None:
        if org_style == "nested":
            claims["o"] = {"id": org_id, "rol": "admin"}
        else:
            claims["org_id"] = org_id
    return jwt.encode(claims, private_pem, algorithm="RS256", headers={"kid": _TEST_KID})


def _build_app(public_key, issuer: str) -> FastAPI:
    """Minimal app with one protected route + the real middleware wired to
    the fake JWKS client. Mirrors how app/main.py mounts it."""
    app = FastAPI()

    @app.get("/api/v1/jobs")
    async def list_jobs(request: Request):
        return {
            "user_id": request.state.user_id,
            "org_id": request.state.org_id,
        }

    @app.post("/api/v1/billing/webhook")
    async def webhook(request: Request):
        # Stand-in for the (deferred) Stripe handler: 400 on missing sig,
        # exactly as test_stripe_webhook_idempotency expects post-D4.
        if "stripe-signature" not in request.headers:
            from starlette.responses import JSONResponse

            return JSONResponse(status_code=400, content={"code": "missing_signature"})
        return {"status": "processed"}

    @app.get("/api/v1/health")
    async def health():
        return {"ok": True}

    app.add_middleware(
        ClerkJWTMiddleware,
        jwks_client=_FakeJWKSClient(public_key),
        issuer=issuer,
        jwks_url="https://relevant-mosquito-93.clerk.accounts.dev/.well-known/jwks.json",
    )
    return app


# ----------------------- JWT verification scenarios -----------------------


@pytest.mark.asyncio
async def test_valid_jwt_extracts_org_id_into_request_state(
    session,
    test_keypair,
    issuer_url,
):
    """Happy path: valid JWT with org claim → 200 and
    request.state.{user_id,org_id} populated. Both org claim styles."""
    private_pem, public_key = test_keypair
    app = _build_app(public_key, issuer_url)
    client = TestClient(app)

    for style in ("flat", "nested"):
        token = _mint(
            private_pem,
            issuer=issuer_url,
            sub="user_ALICE",
            org_id="org_TEST_VALID",
            org_style=style,
        )
        resp = client.get("/api/v1/jobs", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200, (style, resp.text)
        body = resp.json()
        assert body["org_id"] == "org_TEST_VALID"
        assert body["user_id"] == "user_ALICE"


@pytest.mark.asyncio
async def test_expired_jwt_returns_401_no_state_mutation(
    session,
    test_keypair,
    issuer_url,
):
    """exp in the past → 401 before the route runs."""
    private_pem, public_key = test_keypair
    app = _build_app(public_key, issuer_url)
    client = TestClient(app)

    token = _mint(private_pem, issuer=issuer_url, exp_delta=-1)
    resp = client.get("/api/v1/jobs", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401
    assert resp.json()["code"] == "token_expired"


@pytest.mark.asyncio
async def test_wrong_issuer_jwt_returns_401_no_state_mutation(
    session,
    test_keypair,
    issuer_url,
):
    """Signature verifies against our key BUT iss is an attacker's project
    → 401. Pins the issuer-claim check (clerk_auth.py issuer kwarg)."""
    private_pem, public_key = test_keypair
    app = _build_app(public_key, issuer_url)
    client = TestClient(app)

    token = _mint(private_pem, issuer="https://attacker-evil-42.clerk.accounts.dev")
    resp = client.get("/api/v1/jobs", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401
    assert resp.json()["code"] == "invalid_issuer"


@pytest.mark.asyncio
async def test_missing_org_id_claim_returns_403_authn_ok_authz_fail(
    session,
    test_keypair,
    issuer_url,
):
    """Valid sig + issuer but NO org claim → 403 (not 401, not a
    synthesized personal org). Source-grounded: this stub + models.py:
    141-148. Resolves the brief's [DECISION NEEDED] in favor of 403."""
    private_pem, public_key = test_keypair
    app = _build_app(public_key, issuer_url)
    client = TestClient(app)

    token = _mint(private_pem, issuer=issuer_url, org_id=None)
    resp = client.get("/api/v1/jobs", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403
    assert resp.json()["code"] == "no_organization"


@pytest.mark.asyncio
async def test_malformed_authorization_header_returns_401(
    session,
    test_keypair,
    issuer_url,
):
    """Every malformed-header shape → 401, no JWKS touched."""
    _, public_key = test_keypair
    app = _build_app(public_key, issuer_url)
    client = TestClient(app)

    cases = [
        {},  # no header at all
        {"Authorization": "Token abc"},  # wrong scheme
        {"Authorization": "Bearer"},  # no token
        {"Authorization": "Bearer "},  # trailing space, empty token
        {"Authorization": "Bearer  abc"},  # double-space copy-paste bug
    ]
    for headers in cases:
        resp = client.get("/api/v1/jobs", headers=headers)
        assert resp.status_code == 401, headers
        assert resp.json()["code"] in {
            "missing_authorization",
            "invalid_authorization",
        }


@pytest.mark.asyncio
async def test_webhook_route_is_exempt_from_jwt_middleware(
    session,
    test_keypair,
    issuer_url,
):
    """POST /api/v1/billing/webhook with NO Authorization → must NOT 401;
    reaches the handler which 400s on the missing Stripe-Signature
    (stripe_webhook_spec.md:240 + D4_specs.md §3.1)."""
    _, public_key = test_keypair
    app = _build_app(public_key, issuer_url)
    client = TestClient(app)

    resp = client.post("/api/v1/billing/webhook", content=b"{}")
    assert resp.status_code == 400, resp.text
    assert resp.json()["code"] == "missing_signature"

    # And /api/v1/health is exempt too (sanity for the exemption list).
    h = client.get("/api/v1/health")
    assert h.status_code == 200


@pytest.mark.asyncio
async def test_bad_signature_returns_401(session, test_keypair, issuer_url):
    """A token signed by a DIFFERENT key than the JWKS provides → 401.
    Extra scenario beyond the 6 stubs: closes the forged-signature gap the
    issuer test alone doesn't (issuer test reuses our key)."""
    _, public_key = test_keypair
    app = _build_app(public_key, issuer_url)
    client = TestClient(app)

    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    other_pem = other_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    token = _mint(other_pem, issuer=issuer_url)
    resp = client.get("/api/v1/jobs", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401
    assert resp.json()["code"] == "invalid_token"


def test_extract_org_id_helper_prefers_nested_then_flat():
    """Unit: `_extract_org_id` precedence (v2 `o.id` → flat `org_id`)."""
    from app.api.clerk_auth import _extract_org_id

    assert _extract_org_id({"o": {"id": "org_V2"}}) == "org_V2"
    assert _extract_org_id({"org_id": "org_V1"}) == "org_V1"
    assert _extract_org_id({"o": {"id": "org_V2"}, "org_id": "org_V1"}) == "org_V2"
    assert _extract_org_id({}) is None
    assert _extract_org_id({"o": {}}) is None
    assert _extract_org_id({"org_id": ""}) is None
    assert _extract_org_id({"o": {"id": str(uuid.uuid4())}}) is not None
