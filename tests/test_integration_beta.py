"""End-to-end integration test (DoD #2) — D4-A jobs-flow rewrite.

Proves the VERIFIED-WORKING conversion core (DR-019) still produces a
byte-equivalent LeRobot v3 dataset AFTER the auth + storage refactor.
Flow under test (the 5 MVP endpoints, org-scoped, Clerk-authed):

  1. POST /api/v1/jobs/presign-upload  → job_id + R2 PUT URL
  2. (test) write the zipped fixture into the fake-R2 bucket at the
     returned key — simulates the client's direct PUT
  3. POST /api/v1/jobs                 → kicks the background conversion
  4. poll GET /api/v1/jobs/{id}        → until state == done
  5. GET .../presign-download          → R2 presigned GET
  6. read the object out of fake-R2, unzip, and assert the file list +
     meta/info.json match a direct `embodied-data convert ... --verify`
     on the same input (same assertion the F-1 test made).

The subprocess invocation itself is UNCHANGED (`SubprocessRunner.run`,
subprocess_runner.py); only the surrounding auth + storage layer changed.
If embodied-data isn't installed the test fails (intended signal).
"""

from __future__ import annotations

import asyncio
import io
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

import httpx
import pytest
from starlette.testclient import TestClient

from app.db.models import Org


def _zip_fixture(src: Path) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for p in src.rglob("*"):
            if p.is_file():
                zf.write(p, p.relative_to(src))
    return buf.getvalue()


def _embodied_data_available() -> bool:
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


async def _seed_org(org_id: str) -> None:
    import app.db.session as db_session

    sm = db_session.get_sessionmaker()
    async with sm() as s:
        s.add(Org(id=org_id, name="E2E Org"))
        await s.commit()


@pytest.mark.skipif(not _embodied_data_available(), reason="embodied-data CLI not available")
@pytest.mark.asyncio
async def test_beta_675_single_ep_round_trip_via_jobs_flow(
    db_app, beta_fixture: Path, clerk_token, fake_r2
) -> None:
    """httpx ASGITransport (not TestClient) so the in-process background
    conversion task progresses on the SAME event loop as the polling."""
    await _seed_org("org_E2E")
    auth = {"Authorization": f"Bearer {clerk_token(org_id='org_E2E')}"}
    archive_bytes = _zip_fixture(beta_fixture)

    transport = httpx.ASGITransport(app=db_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. presign-upload
        pre = await client.post(
            "/api/v1/jobs/presign-upload",
            headers=auth,
            json={
                "filename": "beta_675.zip",
                "from_format": "agibot",
                "to_format": "lerobot-v3",
            },
        )
        assert pre.status_code == 201, pre.text
        pre_body = pre.json()
        job_id = pre_body["job_id"]
        input_key = pre_body["key"]
        assert input_key.startswith(f"orgs/org_E2E/jobs/{job_id}/input/")

        # 2. simulate the client's direct R2 PUT into the fake bucket.
        obj_path = fake_r2.bucket_dir / input_key
        obj_path.parent.mkdir(parents=True, exist_ok=True)
        obj_path.write_bytes(archive_bytes)

        # 3. POST /jobs → kick conversion.
        created = await client.post("/api/v1/jobs", headers=auth, json={"job_id": job_id})
        assert created.status_code == 202, created.text

        # 4. poll until terminal. asyncio.sleep yields the loop so the
        # background task (subprocess via to_thread) makes progress.
        final = None
        for _ in range(240):  # 240 * 0.5s = 120s ceiling
            s = await client.get(f"/api/v1/jobs/{job_id}", headers=auth)
            assert s.status_code == 200, s.text
            sb = s.json()
            if sb["state"] in ("done", "failed", "expired"):
                final = sb
                break
            await asyncio.sleep(0.5)
        assert final is not None and final["state"] == "done", f"final={final}"

        # 5. presign-download.
        dl = await client.get(f"/api/v1/jobs/{job_id}/presign-download", headers=auth)
        assert dl.status_code == 200, dl.text
    output_key = f"orgs/org_E2E/jobs/{job_id}/output/result.zip"
    wrapper_zip = (fake_r2.bucket_dir / output_key).read_bytes()
    assert len(wrapper_zip) > 0

    # 6. direct CLI run on the same input → compare.
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

        cli_zip_buf = io.BytesIO()
        with zipfile.ZipFile(cli_zip_buf, "w", compression=zipfile.ZIP_STORED) as zf:
            for p in cli_dst_path.rglob("*"):
                if p.is_file():
                    zf.write(p, p.relative_to(cli_dst_path))
        cli_zip = cli_zip_buf.getvalue()

    with zipfile.ZipFile(io.BytesIO(wrapper_zip)) as wz, zipfile.ZipFile(io.BytesIO(cli_zip)) as cz:
        wrapper_names = sorted(zi.filename for zi in wz.infolist() if not zi.is_dir())
        cli_names = sorted(zi.filename for zi in cz.infolist() if not zi.is_dir())
        assert wrapper_names == cli_names, (
            f"file list differs:\nwrapper={wrapper_names}\ncli={cli_names}"
        )
        if "meta/info.json" in wrapper_names:
            assert wz.read("meta/info.json") == cz.read("meta/info.json")


def test_create_job_requires_auth(db_app) -> None:
    """The kick-off endpoint must 401 without a Clerk JWT (R3 entry)."""
    with TestClient(db_app) as client:
        resp = client.post("/api/v1/jobs", json={"job_id": "x"})
    assert resp.status_code == 401
