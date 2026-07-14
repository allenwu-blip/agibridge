"""Jobs route — auth + org-scoping + status shape (D4-A Refactor #3).

Replaces the deprecated F-1 `/api/v1/status/{session_id}` tests. The
`_estimated_progress_pct` helper lived in the now-deprecated
`app/api/status.py`; Cycle H §3 polls on `state` (not a pct), so the new
`GET /api/v1/jobs/{job_id}` returns state, not a progress percent. These
tests pin the new contract: auth required, org-scoped 404, valid-shape.
"""

from __future__ import annotations

import uuid

import pytest
from starlette.testclient import TestClient

from app.db.job_store import JobStore
from app.db.models import Org


@pytest.fixture
def client(db_app) -> TestClient:
    return TestClient(db_app)


async def _seed_org(monkeypatch, org_id: str = "org_TEST"):
    """Insert the Org row so FK(jobs.org_id → orgs.id) holds."""
    import app.db.session as db_session

    sm = db_session.get_sessionmaker()
    async with sm() as s:
        s.add(Org(id=org_id, name="Test Org"))
        await s.commit()


def test_unauthenticated_jobs_list_returns_401(client: TestClient) -> None:
    resp = client.get("/api/v1/jobs")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_get_unknown_job_returns_404(client: TestClient, clerk_token) -> None:
    await _seed_org(None)
    token = clerk_token(org_id="org_TEST")
    resp = client.get(
        f"/api/v1/jobs/{uuid.uuid4()}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "job_not_found"


@pytest.mark.asyncio
async def test_get_malformed_job_id_returns_404(client: TestClient, clerk_token) -> None:
    await _seed_org(None)
    token = clerk_token(org_id="org_TEST")
    resp = client.get(
        "/api/v1/jobs/not-a-uuid",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_list_jobs_is_org_scoped(client: TestClient, clerk_token) -> None:
    """R3 at the HTTP boundary: org B never sees org A's jobs even though
    both query `GET /api/v1/jobs`."""
    import app.db.session as db_session

    sm = db_session.get_sessionmaker()
    async with sm() as s:
        s.add_all([Org(id="org_AAA", name="A"), Org(id="org_BBB", name="B")])
        await s.commit()
    async with sm() as s:
        store = JobStore(s)
        await store.create(
            org_id="org_AAA", user_id=None, from_format="agibot", to_format="lerobot-v3"
        )
        await store.create(
            org_id="org_AAA", user_id=None, from_format="agibot", to_format="lerobot-v3"
        )
        await s.commit()

    a_resp = client.get(
        "/api/v1/jobs",
        headers={"Authorization": f"Bearer {clerk_token(org_id='org_AAA')}"},
    )
    assert a_resp.status_code == 200
    assert len(a_resp.json()["jobs"]) == 2

    b_resp = client.get(
        "/api/v1/jobs",
        headers={"Authorization": f"Bearer {clerk_token(org_id='org_BBB')}"},
    )
    assert b_resp.status_code == 200
    assert b_resp.json()["jobs"] == []


@pytest.mark.asyncio
async def test_cross_org_job_read_returns_404_not_403(client: TestClient, clerk_token) -> None:
    """A job_id leaked across orgs must be 404 (side-channel-safe), never
    403 — mirrors job_store.py:69-78 contract at the HTTP layer."""
    import app.db.session as db_session

    sm = db_session.get_sessionmaker()
    async with sm() as s:
        s.add_all([Org(id="org_AAA", name="A"), Org(id="org_BBB", name="B")])
        await s.commit()
    async with sm() as s:
        store = JobStore(s)
        job = await store.create(
            org_id="org_AAA", user_id=None, from_format="agibot", to_format="lerobot-v3"
        )
        await s.commit()
        job_id = job.id

    resp = client.get(
        f"/api/v1/jobs/{job_id}",
        headers={"Authorization": f"Bearer {clerk_token(org_id='org_BBB')}"},
    )
    assert resp.status_code == 404
