"""Track 4 — real-customer edge hardening.

Every boundary a paying customer actually hits must produce a CLEAR typed
error, never a raw 500 / silent failure. This file pins the backend half of
the Track 4 fixes:

1. Oversized upload  -> `POST /jobs/presign-upload` returns a clean 413
   `upload_too_large` BEFORE the R2 URL is issued (the gate is tier-aware,
   `billing_config.MAX_UPLOAD_BYTES` / `tier_gating.max_upload_bytes_for_tier`).
2. Bad / corrupt archive -> `_extract_archive` raises the typed
   `BadArchiveError`, and `_run_conversion` maps it to a `bad_archive`
   failed job with a readable `error_msg` — NOT the opaque `internal_error`
   / `"ValueError"` row it produced before.
3. Quota / soft-cap hit -> `POST /jobs` returns a clean 402
   `soft_cap_exceeded` (HTTP-boundary coverage; the unit-level gate is in
   `test_tier_gating.py`).

Test infra parity with `test_status.py` (TestClient on the in-memory
`db_app`, Clerk-JWT minter) and `test_tier_gating.py` (sqlite+aiosqlite).
"""

from __future__ import annotations

import io
import zipfile

import pytest
from starlette.testclient import TestClient

from app.api.billing_config import MAX_UPLOAD_BYTES
from app.api.jobs import BadArchiveError, _extract_archive
from app.db.job_store import JobStore
from app.db.models import Org, OrgTier


@pytest.fixture
def client(db_app) -> TestClient:
    return TestClient(db_app)


async def _seed_org(org_id: str = "org_TEST", tier: OrgTier = OrgTier.free) -> None:
    """Insert the Org row (FK jobs.org_id -> orgs.id) at a given tier."""
    import app.db.session as db_session

    sm = db_session.get_sessionmaker()
    async with sm() as s:
        s.add(Org(id=org_id, name="Edge Org", tier=tier))
        await s.commit()


# ----------------------- Edge 1: oversized upload -----------------------


@pytest.mark.asyncio
async def test_presign_upload_rejects_oversized_file_with_clean_413(
    client: TestClient, clerk_token
) -> None:
    """A file one byte over the Free-tier cap gets a clean typed 413 — not a
    500, not a silently-issued R2 URL."""
    await _seed_org(tier=OrgTier.free)
    over = MAX_UPLOAD_BYTES[OrgTier.free] + 1
    resp = client.post(
        "/api/v1/jobs/presign-upload",
        headers={"Authorization": f"Bearer {clerk_token(org_id='org_TEST')}"},
        json={
            "filename": "huge.tar.gz",
            "from_format": "agibot",
            "to_format": "lerobot-v3",
            "size_bytes": over,
        },
    )
    assert resp.status_code == 413, resp.text
    detail = resp.json()["detail"]
    assert detail["code"] == "upload_too_large"
    # Copy is readable + names the concrete limit + a path forward.
    assert "free" in detail["message"].lower()
    assert "MB" in detail["message"]
    assert detail["suggestion"] is not None


@pytest.mark.asyncio
async def test_presign_upload_allows_file_at_the_cap(
    client: TestClient, clerk_token, fake_r2
) -> None:
    """A file exactly at the cap is allowed — the gate is `>` not `>=`."""
    await _seed_org(tier=OrgTier.free)
    resp = client.post(
        "/api/v1/jobs/presign-upload",
        headers={"Authorization": f"Bearer {clerk_token(org_id='org_TEST')}"},
        json={
            "filename": "ok.tar.gz",
            "from_format": "agibot",
            "to_format": "lerobot-v3",
            "size_bytes": MAX_UPLOAD_BYTES[OrgTier.free],
        },
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["upload_url"]


@pytest.mark.asyncio
async def test_presign_upload_without_size_skips_the_gate(
    client: TestClient, clerk_token, fake_r2
) -> None:
    """An older client that omits `size_bytes` still works (back-compat) —
    the size gate is opt-in on the declared size."""
    await _seed_org(tier=OrgTier.free)
    resp = client.post(
        "/api/v1/jobs/presign-upload",
        headers={"Authorization": f"Bearer {clerk_token(org_id='org_TEST')}"},
        json={
            "filename": "legacy.tar.gz",
            "from_format": "agibot",
            "to_format": "lerobot-v3",
        },
    )
    assert resp.status_code == 201, resp.text


@pytest.mark.asyncio
async def test_presign_upload_size_gate_is_tier_aware(
    client: TestClient, clerk_token, fake_r2
) -> None:
    """A file that exceeds the Free cap but fits the Solo cap is allowed for
    a Solo-tier org — the gate reads the server-trusted tier, not Free."""
    await _seed_org(org_id="org_SOLO", tier=OrgTier.solo)
    between = MAX_UPLOAD_BYTES[OrgTier.free] + 1
    assert between <= (MAX_UPLOAD_BYTES[OrgTier.solo] or 0)
    resp = client.post(
        "/api/v1/jobs/presign-upload",
        headers={"Authorization": f"Bearer {clerk_token(org_id='org_SOLO')}"},
        json={
            "filename": "big.tar.gz",
            "from_format": "agibot",
            "to_format": "lerobot-v3",
            "size_bytes": between,
        },
    )
    assert resp.status_code == 201, resp.text


# ----------------------- Edge 2: bad / corrupt archive -----------------------


def test_extract_archive_rejects_non_archive_with_typed_error(tmp_path) -> None:
    """A plain text file renamed `.tar.gz` raises the typed `BadArchiveError`
    with a readable reason — NOT a bare ValueError that maps to the opaque
    `internal_error` failed row."""
    bogus = tmp_path / "upload.archive"
    bogus.write_bytes(b"this is not an archive, just some text\n")
    dest = tmp_path / "out"
    dest.mkdir()
    with pytest.raises(BadArchiveError) as exc:
        _extract_archive(bogus, dest)
    assert "archive" in str(exc.value).lower()


def test_extract_archive_rejects_corrupt_zip(tmp_path) -> None:
    """A truncated zip (valid magic bytes, garbage body) raises
    `BadArchiveError`, not an unhandled `zipfile.BadZipFile`."""
    corrupt = tmp_path / "upload.archive"
    # ZIP local-file-header magic, then truncated garbage.
    corrupt.write_bytes(b"\x50\x4b\x03\x04" + b"\x00" * 8)
    dest = tmp_path / "out"
    dest.mkdir()
    with pytest.raises(BadArchiveError) as exc:
        _extract_archive(corrupt, dest)
    assert "corrupt" in str(exc.value).lower() or "incomplete" in str(exc.value).lower()


def test_extract_archive_rejects_corrupt_gzip(tmp_path) -> None:
    """A file with the gzip magic but a non-tar body raises `BadArchiveError`
    rather than leaking a `tarfile.ReadError`."""
    corrupt = tmp_path / "upload.archive"
    corrupt.write_bytes(b"\x1f\x8b" + b"\x00" * 16)
    dest = tmp_path / "out"
    dest.mkdir()
    with pytest.raises(BadArchiveError):
        _extract_archive(corrupt, dest)


def test_extract_archive_accepts_a_real_zip(tmp_path) -> None:
    """Control: a genuine zip still extracts — the hardening did not break
    the happy path."""
    archive = tmp_path / "upload.archive"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("dataset/info.json", '{"ok": true}')
    archive.write_bytes(buf.getvalue())
    dest = tmp_path / "out"
    dest.mkdir()
    _extract_archive(archive, dest)
    assert (dest / "dataset" / "info.json").read_text() == '{"ok": true}'


@pytest.mark.asyncio
async def test_bad_archive_lands_job_in_failed_with_readable_error(
    db_app, clerk_token, fake_r2
) -> None:
    """End-to-end: presign -> PUT a NON-archive into the fake R2 bucket ->
    POST /jobs -> poll. The job must land `failed` with `error_code` =
    `bad_archive` and a customer-readable `error_msg` — not a blank failed
    row and not `internal_error`."""
    import asyncio

    import httpx

    await _seed_org(org_id="org_BAD", tier=OrgTier.free)
    auth = {"Authorization": f"Bearer {clerk_token(org_id='org_BAD')}"}

    transport = httpx.ASGITransport(app=db_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        pre = await c.post(
            "/api/v1/jobs/presign-upload",
            headers=auth,
            json={
                "filename": "not_really.zip",
                "from_format": "agibot",
                "to_format": "lerobot-v3",
                "size_bytes": 42,
            },
        )
        assert pre.status_code == 201, pre.text
        job_id = pre.json()["job_id"]
        input_key = pre.json()["key"]

        # The "uploaded" object is plain text — a corrupt/wrong file.
        obj = fake_r2.bucket_dir / input_key
        obj.parent.mkdir(parents=True, exist_ok=True)
        obj.write_bytes(b"definitely not an AgiBot dataset archive")

        created = await c.post("/api/v1/jobs", headers=auth, json={"job_id": job_id})
        assert created.status_code == 202, created.text

        final = None
        for _ in range(120):  # 60s ceiling
            s = await c.get(f"/api/v1/jobs/{job_id}", headers=auth)
            assert s.status_code == 200, s.text
            sb = s.json()
            if sb["state"] in ("done", "failed", "expired"):
                final = sb
                break
            await asyncio.sleep(0.5)

    assert final is not None, "job never reached a terminal state"
    assert final["state"] == "failed", f"final={final}"
    assert final["error_code"] == "bad_archive", f"final={final}"
    # Readable, customer-facing — names the dataset type, never a stack trace.
    assert "AgiBot/LeRobot" in final["error_msg"]
    assert "ValueError" not in (final["error_msg"] or "")


# ----------------------- Edge 4: quota / soft-cap at the HTTP boundary -----------------------


@pytest.mark.asyncio
async def test_create_job_over_soft_cap_returns_clean_402(db_app, clerk_token, fake_r2) -> None:
    """A Free org already at its monthly job cap that creates one more job
    gets a clean typed 402 `soft_cap_exceeded` from `POST /jobs` — not a
    500. The copy names the cap number and a path to upgrade."""
    import httpx

    from app.api.billing_config import MONTHLY_JOB_SOFT_CAP

    cap = MONTHLY_JOB_SOFT_CAP[OrgTier.free]
    assert cap is not None
    await _seed_org(org_id="org_CAP", tier=OrgTier.free)

    # Pre-seed `cap` existing jobs so the next presign+create is the (cap+1)th
    # this calendar month — over the limit.
    import app.db.session as db_session

    sm = db_session.get_sessionmaker()
    async with sm() as s:
        store = JobStore(s)
        for _ in range(cap):
            await store.create(
                org_id="org_CAP",
                user_id=None,
                from_format="agibot",
                to_format="lerobot-v3",
            )
        await s.commit()

    auth = {"Authorization": f"Bearer {clerk_token(org_id='org_CAP')}"}
    transport = httpx.ASGITransport(app=db_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        pre = await c.post(
            "/api/v1/jobs/presign-upload",
            headers=auth,
            json={
                "filename": "one_too_many.tar.gz",
                "from_format": "agibot",
                "to_format": "lerobot-v3",
            },
        )
        assert pre.status_code == 201, pre.text
        job_id = pre.json()["job_id"]
        input_key = pre.json()["key"]
        obj = fake_r2.bucket_dir / input_key
        obj.parent.mkdir(parents=True, exist_ok=True)
        obj.write_bytes(b"\x1f\x8b" + b"\x00" * 16)  # body irrelevant — gate fires first

        created = await c.post("/api/v1/jobs", headers=auth, json={"job_id": job_id})

    assert created.status_code == 402, created.text
    detail = created.json()["detail"]
    assert detail["code"] == "soft_cap_exceeded"
    assert str(cap) in detail["message"]
    assert detail["suggestion"] is not None
    assert "upgrade" in detail["suggestion"].lower()
