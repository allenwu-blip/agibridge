"""GET /api/v1/health and /api/v1/version."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_health_returns_lib_version(client: TestClient) -> None:
    resp = client.get("/api/v1/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    # Spec v2.2 §2.4 / Phase D C1: importlib.metadata, NOT subprocess.
    # Pinned at 0.3.1 in pyproject.toml.
    assert body["embodied_data_version"] == "0.3.1"
    assert "uptime_s" in body
    assert "free_disk_mb" in body


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
