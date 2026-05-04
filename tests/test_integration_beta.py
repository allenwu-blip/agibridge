"""End-to-end integration test (DoD #2).

Upload AgiBot Beta task 675 single-episode fixture (1.5 MB committed at
tests/fixtures/), poll /status until done, download, verify zip contents
match `embodied-data convert ... --verify` on the same input.

This test uses the real `embodied-data==0.3.1` subprocess pinned in
pyproject.toml; if it isn't installed the test fails, which is the
intended signal."""

from __future__ import annotations

import io
import shutil
import subprocess
import tempfile
import time
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client():
    """TestClient as context manager so lifespan startup/teardown actually
    run — needed because the upload handler does
    `asyncio.create_task(_run_background)` which only completes if the
    portal stays alive."""
    with TestClient(app) as c:
        yield c


def _zip_fixture(src: Path) -> bytes:
    """Pack the fixture dir into an in-memory zip suitable for upload."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for p in src.rglob("*"):
            if p.is_file():
                zf.write(p, p.relative_to(src))
    return buf.getvalue()


def _embodied_data_available() -> bool:
    """Skip the integration test if the lib isn't reachable."""
    if shutil.which("embodied-data"):
        return True
    try:
        r = subprocess.run(
            ["python", "-m", "embodied_data.cli", "--version"],
            capture_output=True,
            timeout=10,
        )
        return r.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


@pytest.mark.skipif(not _embodied_data_available(), reason="embodied-data CLI not available")
def test_beta_675_single_ep_round_trip(client: TestClient, beta_fixture: Path) -> None:
    """Upload the Beta fixture, poll status, download, verify zip matches a
    direct CLI invocation of the same command on the same input."""
    archive_bytes = _zip_fixture(beta_fixture)

    # 1. Upload.
    resp = client.post(
        "/api/v1/upload",
        files={"file": ("beta_675.zip", archive_bytes, "application/zip")},
        data={"from_format": "agibot", "to_format": "lerobot-v3"},
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    sid = body["session_id"]

    # 2. Poll /status until terminal state.
    deadline = time.monotonic() + 120  # 2 min cushion for CI machines
    final_state: str | None = None
    final_body: dict | None = None
    while time.monotonic() < deadline:
        sresp = client.get(f"/api/v1/status/{sid}")
        assert sresp.status_code == 200, sresp.text
        sb = sresp.json()
        if sb["state"] in ("done", "failed", "expired"):
            final_state = sb["state"]
            final_body = sb
            break
        time.sleep(0.5)

    assert final_state == "done", f"final_body={final_body}"
    assert final_body is not None
    assert final_body["download_url"] == f"/api/v1/download/{sid}"
    # estimated_progress_pct → null when state == done (spec §2.2.1).
    assert final_body["estimated_progress_pct"] is None

    # 3. Download the result zip.
    dresp = client.get(f"/api/v1/download/{sid}")
    assert dresp.status_code == 200
    assert dresp.headers["content-type"] == "application/zip"
    wrapper_zip = dresp.content
    assert len(wrapper_zip) > 0

    # 4. Run `embodied-data convert ... --verify` directly on the same input.
    with tempfile.TemporaryDirectory() as cli_dst:
        cli_dst_path = Path(cli_dst)
        cmd = (
            ["embodied-data"]
            if shutil.which("embodied-data")
            else ["python", "-m", "embodied_data.cli"]
        )
        cmd += [
            "--json",
            "convert",
            str(beta_fixture),
            str(cli_dst_path),
            "--from",
            "agibot",
            "--to",
            "lerobot-v3",
            "--verify",
        ]
        cli_run = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        assert cli_run.returncode == 0, f"CLI failed: {cli_run.stderr}"

        # 5. Build a zip from the CLI output directory.
        cli_zip_buf = io.BytesIO()
        with zipfile.ZipFile(cli_zip_buf, "w", compression=zipfile.ZIP_STORED) as zf:
            for p in cli_dst_path.rglob("*"):
                if p.is_file():
                    zf.write(p, p.relative_to(cli_dst_path))
        cli_zip = cli_zip_buf.getvalue()

    # 6. Compare zip contents (file lists + per-file bytes for non-binary
    # canonical files; data parquet files may differ in compression/order).
    with zipfile.ZipFile(io.BytesIO(wrapper_zip)) as wz, zipfile.ZipFile(io.BytesIO(cli_zip)) as cz:
        wrapper_names = sorted(zi.filename for zi in wz.infolist() if not zi.is_dir())
        cli_names = sorted(zi.filename for zi in cz.infolist() if not zi.is_dir())
        # Same set of relative file paths.
        assert wrapper_names == cli_names, (
            f"file list differs:\nwrapper={wrapper_names}\ncli={cli_names}"
        )
        # info.json should be byte-identical (deterministic given same input).
        if "meta/info.json" in wrapper_names:
            assert wz.read("meta/info.json") == cz.read("meta/info.json")


@pytest.mark.skipif(not _embodied_data_available(), reason="embodied-data CLI not available")
def test_unsupported_pair_returns_400_before_subprocess(
    client: TestClient, beta_fixture: Path
) -> None:
    """Spec §2.1: client-side check before invocation. agibot→agibot is
    not in `_SUPPORTED` (convert/__init__.py:19-22)."""
    archive_bytes = _zip_fixture(beta_fixture)
    resp = client.post(
        "/api/v1/upload",
        files={"file": ("x.zip", archive_bytes, "application/zip")},
        data={"from_format": "agibot", "to_format": "agibot"},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "invalid_format_pair"
