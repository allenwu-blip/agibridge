"""Presign-upload validation (D4-A Refactor #3).

Replaces the deprecated F-1 multipart `/api/v1/upload` validation tests.
The new flow is presign-then-PUT-to-R2; format-pair validation now lives
in `POST /api/v1/jobs/presign-upload` (jobs.py). Magic-byte / zip-bomb /
path-traversal guards moved into the background `_extract_archive`
(jobs.py) which runs server-side after the R2 PUT — those are exercised
by the E2E test (test_integration_beta.py). Here we pin the early
fail-fast: auth required + format-pair rejection before any R2 work.
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from app.db.models import Org


@pytest.fixture
def client(db_app) -> TestClient:
    return TestClient(db_app)


async def _seed_org(org_id: str = "org_TEST") -> None:
    import app.db.session as db_session

    sm = db_session.get_sessionmaker()
    async with sm() as s:
        s.add(Org(id=org_id, name="Test Org"))
        await s.commit()


def test_presign_requires_auth(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/jobs/presign-upload",
        json={"filename": "a.zip", "from_format": "agibot", "to_format": "lerobot-v3"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_invalid_format_pair_same(client: TestClient, clerk_token) -> None:
    await _seed_org()
    resp = client.post(
        "/api/v1/jobs/presign-upload",
        headers={"Authorization": f"Bearer {clerk_token(org_id='org_TEST')}"},
        json={"filename": "a.zip", "from_format": "agibot", "to_format": "agibot"},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "invalid_format_pair"


@pytest.mark.asyncio
async def test_invalid_format_pair_unknown(client: TestClient, clerk_token) -> None:
    """Unsupported pair → 400 (mirrors convert/__init__.py:19-22)."""
    await _seed_org()
    resp = client.post(
        "/api/v1/jobs/presign-upload",
        headers={"Authorization": f"Bearer {clerk_token(org_id='org_TEST')}"},
        json={"filename": "a.zip", "from_format": "agibot", "to_format": "h5ad"},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "invalid_format_pair"


@pytest.mark.asyncio
async def test_missing_required_field_returns_422(client: TestClient, clerk_token) -> None:
    await _seed_org()
    resp = client.post(
        "/api/v1/jobs/presign-upload",
        headers={"Authorization": f"Bearer {clerk_token(org_id='org_TEST')}"},
        json={"filename": "a.zip", "from_format": "agibot"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_valid_presign_returns_url_and_job_id(
    client: TestClient, clerk_token, fake_r2
) -> None:
    """Happy path: valid pair → 201 with job_id + R2 presigned PUT URL.
    The returned key MUST be under the JWT org's prefix (R3)."""
    await _seed_org("org_TEST")
    resp = client.post(
        "/api/v1/jobs/presign-upload",
        headers={"Authorization": f"Bearer {clerk_token(org_id='org_TEST')}"},
        json={"filename": "beta.zip", "from_format": "agibot", "to_format": "lerobot-v3"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["job_id"]
    assert body["key"].startswith(f"orgs/org_TEST/jobs/{body['job_id']}/input/")
    assert "upload_url" in body
    assert body["expires_in"] > 0
