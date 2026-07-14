"""Shared fixtures.

`isolated_root` keeps SessionStore-style scratch dirs out of the real
/tmp. D4-A adds fixtures for the new commercial surface: an in-memory
DB-backed app, a Clerk-JWT auth header minter, and a local-filesystem
fake R2 client (no moto dependency — moto stays out of D4-A scope per the
Stripe-excluded brief; the R2 boundary moto stubs remain # IMPLEMENT IN D4
in tests/test_r2_boundaries.py, untouched).
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path

import jwt
import pytest
import pytest_asyncio
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.models import Base

_TEST_ISSUER = "https://relevant-mosquito-93.clerk.accounts.dev"
_TEST_KID = "test-key-1"


@pytest.fixture(autouse=True)
def isolated_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Point scratch-dir root at a per-test tmp dir."""
    root = tmp_path / "agibridge_root"
    root.mkdir()
    monkeypatch.setenv("AGIBRIDGE_ROOT", str(root))
    yield root


@pytest.fixture
def beta_fixture() -> Path:
    """Path to the committed AgiBot Beta task 675 single-episode fixture."""
    p = Path(__file__).parent / "fixtures" / "agibot_beta_675_single_ep"
    assert p.is_dir(), f"fixture missing: {p}"
    assert (p / "task_info_675.json").is_file()
    assert (p / "675" / "936938" / "proprio_stats.h5").is_file()
    return p


# --------------------------------------------------------------------------
# D4-A: commercial-surface fixtures
# --------------------------------------------------------------------------


@pytest.fixture(scope="session")
def _rsa_keypair():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return pem, key.public_key()


@pytest.fixture
def clerk_token(_rsa_keypair):
    """Factory: mint an RS256 Clerk-style JWT for a given org/user."""
    private_pem, _ = _rsa_keypair

    def _make(*, org_id: str = "org_TEST", user_id: str = "user_TEST") -> str:
        now = int(time.time())
        return jwt.encode(
            {
                "sub": user_id,
                "iss": _TEST_ISSUER,
                "iat": now,
                "exp": now + 300,
                "org_id": org_id,
            },
            private_pem,
            algorithm="RS256",
            headers={"kid": _TEST_KID},
        )

    return _make


@pytest_asyncio.fixture
async def db_app(_rsa_keypair, monkeypatch):
    """Build `app.main.app` wired to a fresh in-memory sqlite engine and the
    Clerk middleware with a fake JWKS client (network-pure). Yields
    `(app, public_key)` for a TestClient.

    Schema is created via `Base.metadata.create_all` (sqlite parity posture
    per test_r3_cross_org_isolation.py:13-17).
    """
    _, public_key = _rsa_keypair

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, expire_on_commit=False)

    # Patch the DB layer's sessionmaker/engine BEFORE importing app.main so
    # both the request-scoped get_session and the background task share the
    # same in-memory engine.
    import app.db.session as db_session

    monkeypatch.setattr(db_session, "_engine", engine)
    monkeypatch.setattr(db_session, "_sessionmaker", sm)

    import importlib

    import app.main as main_mod

    importlib.reload(main_mod)

    # Swap the real Clerk middleware's JWKS client for a fake one. The
    # middleware instance lives in app.user_middleware; rebuild with a fake.
    from app.api.clerk_auth import ClerkJWTMiddleware

    class _FakeSigningKey:
        def __init__(self, k):
            self.key = k

    class _FakeJWKS:
        def __init__(self, k):
            self._k = k

        def get_signing_key_from_jwt(self, token):
            return _FakeSigningKey(self._k)

    # Rebuild a clean app so middleware order is deterministic in tests.
    from fastapi import FastAPI

    test_app = FastAPI()
    for r in main_mod.app.router.routes:
        test_app.router.routes.append(r)
    from fastapi.middleware.cors import CORSMiddleware

    test_app.add_middleware(CORSMiddleware, allow_origins=["*"])
    test_app.add_middleware(
        ClerkJWTMiddleware,
        jwks_client=_FakeJWKS(public_key),
        issuer=_TEST_ISSUER,
        jwks_url="https://relevant-mosquito-93.clerk.accounts.dev/.well-known/jwks.json",
    )

    yield test_app

    await engine.dispose()


@pytest.fixture
def fake_r2(monkeypatch, tmp_path):
    """Replace `app.storage.r2._client()` with a local-filesystem fake.

    presign_upload/download return file:// style sentinels; download_file /
    upload_file copy on local disk. Lets the verified subprocess run E2E
    without network or moto.
    """
    import shutil as _shutil

    from app.storage import r2

    bucket_dir = tmp_path / "r2bucket"
    bucket_dir.mkdir()

    class _FakeS3:
        def generate_presigned_url(self, op, Params, ExpiresIn):
            key = Params["Key"]
            return f"https://fake-r2.local/{Params['Bucket']}/{key}?exp={ExpiresIn}"

        def download_file(self, bucket, key, dest):
            src = bucket_dir / key
            _shutil.copyfile(src, dest)

        def upload_file(self, src, bucket, key):
            target = bucket_dir / key
            target.parent.mkdir(parents=True, exist_ok=True)
            _shutil.copyfile(src, target)

    fake = _FakeS3()
    monkeypatch.setattr(r2, "_client", lambda: fake)
    monkeypatch.setattr(r2, "_bucket", lambda: "test-bucket")
    monkeypatch.setenv("R2_BUCKET", "test-bucket")
    # Expose the bucket dir so tests can pre-seed an "uploaded" object.
    fake.bucket_dir = bucket_dir  # type: ignore[attr-defined]
    return fake
