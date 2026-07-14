"""GET /api/v1/health and /api/v1/version."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_health_returns_trimmed_shape(client: TestClient) -> None:
    """D4-harden Check 3b: PUBLIC /health is trimmed to EXACTLY
    {"ok": true, "version": "<embodied-data version>"}. `free_disk_mb`
    (infra info-leak), `uptime_s`, `active_session`, and the old
    `embodied_data_version` key MUST be gone."""
    resp = client.get("/api/v1/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"ok": True, "version": "0.3.1"}  # exact shape, nothing else
    # explicit negative assertions: the leaky/legacy fields are gone
    for leaked in ("free_disk_mb", "uptime_s", "active_session", "embodied_data_version"):
        assert leaked not in body, f"/health still leaks {leaked!r}"


def test_version_endpoint_shape(client: TestClient) -> None:
    """Phase D A1.2: F-1 transparency endpoint."""
    resp = client.get("/api/v1/version")
    assert resp.status_code == 200
    body = resp.json()
    assert "agibridge_git_sha" in body
    assert body["embodied_data_version"] == "0.3.1"
    assert "built_at" in body


def test_version_uses_env_git_sha(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """Dockerfile passes ARG GIT_SHA → env. The endpoint should surface it."""
    monkeypatch.setenv("GIT_SHA", "deadbeef0123")
    monkeypatch.setenv("BUILT_AT", "2026-05-02T00:00:00Z")
    resp = client.get("/api/v1/version")
    body = resp.json()
    assert body["agibridge_git_sha"] == "deadbeef0123"
    assert body["built_at"] == "2026-05-02T00:00:00Z"
