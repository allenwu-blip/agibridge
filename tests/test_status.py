"""Status endpoint + estimated_progress_pct semantics."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.session_store import Session
from app.api.status import _estimated_progress_pct
from app.main import _session_store, app
from app.schemas import State


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
async def session(isolated_root: Path) -> Session:
    store = _session_store
    sess = await store.create("01HX_test_session_aaaa")
    return sess


def test_unknown_session_returns_404(client: TestClient) -> None:
    resp = client.get("/api/v1/status/01HX_does_not_exist_xx")
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "session_not_found"


async def test_estimated_progress_pct_null_when_pending(session: Session) -> None:
    session.state = State.pending
    assert _estimated_progress_pct(session) is None


async def test_estimated_progress_pct_null_when_done(session: Session) -> None:
    session.state = State.done
    session.started_at = datetime.now(UTC)
    session.estimated_total_s = 100
    assert _estimated_progress_pct(session) is None


async def test_estimated_progress_pct_clamped_at_99(session: Session) -> None:
    """Spec §2.2.1: clamp at 99 while running, only flip to 100 on done."""
    session.state = State.running
    # started 1 hour ago, total estimate 1 second → would naively be 360000%.
    session.started_at = datetime.now(UTC) - timedelta(hours=1)
    session.estimated_total_s = 1
    assert _estimated_progress_pct(session) == 99


async def test_estimated_progress_pct_zero_for_no_start(session: Session) -> None:
    session.state = State.running
    session.started_at = None
    session.estimated_total_s = 100
    assert _estimated_progress_pct(session) == 0


async def test_estimated_progress_pct_mid_run(session: Session) -> None:
    session.state = State.running
    session.started_at = datetime.now(UTC) - timedelta(seconds=50)
    session.estimated_total_s = 100
    pct = _estimated_progress_pct(session)
    assert pct is not None
    # Should be ~50; allow scheduler jitter.
    assert 45 <= pct <= 60


async def test_status_endpoint_round_trip(client: TestClient, session: Session) -> None:
    """End-to-end: GET returns the right shape with progress field."""
    session.state = State.running
    session.started_at = datetime.now(UTC) - timedelta(seconds=10)
    session.estimated_total_s = 100
    resp = client.get(f"/api/v1/status/{session.session_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["session_id"] == session.session_id
    assert body["state"] == "running"
    assert body["estimated_progress_pct"] is not None
    assert 0 <= body["estimated_progress_pct"] <= 99
