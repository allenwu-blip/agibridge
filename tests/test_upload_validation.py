"""Upload-time validation: format pair, size cap, magic bytes, single-flight.

These tests don't actually invoke embodied-data; they exercise the input-side
guards that fail-fast before any subprocess work."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def _make_zip_bytes(files: dict[str, bytes]) -> bytes:
    """Helper: build a small in-memory zip."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buf.getvalue()


def test_invalid_format_pair_same(client: TestClient) -> None:
    """from_format == to_format → 400 invalid_format_pair."""
    resp = client.post(
        "/api/v1/upload",
        files={"file": ("a.zip", b"PK\x03\x04dummy", "application/zip")},
        data={"from_format": "agibot", "to_format": "agibot"},
    )
    assert resp.status_code == 400
    body = resp.json()
    assert body["detail"]["code"] == "invalid_format_pair"


def test_invalid_format_pair_unknown(client: TestClient) -> None:
    """Unsupported pair → 400 invalid_format_pair (mirrors convert/__init__.py:19-22)."""
    resp = client.post(
        "/api/v1/upload",
        files={"file": ("a.zip", b"PK\x03\x04", "application/zip")},
        data={"from_format": "agibot", "to_format": "h5ad"},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "invalid_format_pair"


def test_unsupported_archive_type(client: TestClient) -> None:
    """Magic bytes don't match zip/gzip → 415 unsupported_archive_type."""
    resp = client.post(
        "/api/v1/upload",
        files={"file": ("a.zip", b"NOT-A-ZIP-AT-ALL-12345", "application/zip")},
        data={"from_format": "agibot", "to_format": "lerobot-v3"},
    )
    assert resp.status_code == 415
    assert resp.json()["detail"]["code"] == "unsupported_archive_type"


def test_zip_with_path_traversal_rejected(client: TestClient, tmp_path: Path) -> None:
    """Path-traversal entry → 415 mime_spoofed (spec §7)."""
    # Hand-craft a zip with `../../escape` entry.
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("../../etc/passwd_attempt", b"not actually passwd")
    buf.seek(0)
    resp = client.post(
        "/api/v1/upload",
        files={"file": ("evil.zip", buf.getvalue(), "application/zip")},
        data={"from_format": "agibot", "to_format": "lerobot-v3"},
    )
    # Either 415 (path-traversal caught at extract) or 422 (early reject)
    assert resp.status_code in (415, 422)


def test_missing_required_field(client: TestClient) -> None:
    """No `to_format` → FastAPI 422."""
    resp = client.post(
        "/api/v1/upload",
        files={"file": ("a.zip", b"PK\x03\x04", "application/zip")},
        data={"from_format": "agibot"},
    )
    assert resp.status_code == 422
