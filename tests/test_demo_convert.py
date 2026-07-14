"""`POST /api/v1/demo/convert` — zero-signup "try it" demo
(impl at `app/api/demo.py`).

Parity target: `tests/test_events_endpoint.py` (same in-memory sqlite ASGI
posture, same shared-limiter reset discipline) +
`tests/test_integration_beta.py` (the real verified-converter E2E uses
httpx ASGITransport so the in-process subprocess progresses on the same
loop as the request).

Test -> behavior mapping:

| Test                                                  | Asserts                         |
|-------------------------------------------------------|---------------------------------|
| test_demo_returns_real_validated_result_no_signup     | no JWT -> 200 ok + REAL 5-check |
| test_demo_emits_funnel_demo_run_row                   | one funnel_events kind=demo_run |
| test_demo_rejects_request_body                        | any body -> 200 ok:false        |
| test_demo_rate_limited_over_5_per_min                 | 6th in a min -> 429             |
| test_demo_busy_returns_503_when_locked                | lock held -> clean 503 busy     |
| test_demo_never_500_when_converter_unavailable        | converter fault -> 200 ok:false |
| test_demo_no_r3_surface_touched                       | no jobs/usage_events row        |
"""

from __future__ import annotations

import asyncio
import importlib
import shutil
import subprocess

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.rate_limit import limiter
from app.db.models import Base, FunnelEvent, Job, UsageEvent


@pytest.fixture(autouse=True)
def _reset_limiter():
    """slowapi's in-memory window is process-global (test_rate_limit_public
    .py:26-32); clear it so each test starts from a full allowance."""
    limiter.reset()
    yield
    limiter.reset()


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


@pytest.fixture
def db_client(monkeypatch):
    """ASGI TestClient bound to a fresh in-memory engine. Mirrors
    test_events_endpoint.db_client:45-68. Returns (client, sessionmaker)."""
    import app.db.session as db_session

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    async def _mk() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_mk())
    sm = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(db_session, "_engine", engine)
    monkeypatch.setattr(db_session, "_sessionmaker", sm)

    import app.main as main_mod

    importlib.reload(main_mod)
    main_mod.app.state.limiter = limiter
    return TestClient(main_mod.app), sm


async def _count(sm, model) -> int:
    async with sm() as s:
        return int((await s.execute(select(func.count()).select_from(model))).scalar_one() or 0)


# --------------------------------------------------------------------------
# the real-converter E2E (skipped only if embodied-data CLI is unavailable)
# --------------------------------------------------------------------------


@pytest.mark.skipif(not _embodied_data_available(), reason="embodied-data CLI not available")
@pytest.mark.asyncio
async def test_demo_returns_real_validated_result_no_signup(monkeypatch) -> None:
    """No Authorization header at all -> the Clerk middleware must NOT
    short-circuit (demo is exempt), and the response must carry the REAL
    5-check validator output + real dataset shape from a genuine
    AgiBot->LeRobot v3 run on the bundled in-image sample
    (`app/demo_assets/agibot_sample/` — a truncated REAL Beta-675 task).
    httpx ASGITransport so the in-process subprocess progresses on the
    request's loop."""
    import app.db.session as db_session

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(db_session, "_engine", engine)
    monkeypatch.setattr(db_session, "_sessionmaker", sm)

    import app.main as main_mod

    importlib.reload(main_mod)
    main_mod.app.state.limiter = limiter

    transport = httpx.ASGITransport(app=main_mod.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/api/v1/demo/convert", timeout=120)

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True, body
    assert body["state"] == "done"
    assert body["from_format"] == "agibot"
    assert body["to_format"] == "lerobot-v3"
    # REAL validator: the minimized in-image sample produces PASS — the
    # same 3 PASS / 2 SKIP profile as the full fixture (the 2 SKIPs are
    # inherent to a proprio-only dataset, not an artifact of truncation).
    assert body["validator_result"] in ("PASS", "WARN")
    names = {c["name"] for c in body["checks"]}
    assert "schema conformance" in names
    assert "timestamp monotonicity" in names
    assert "action-dim consistency" in names
    # REAL dataset shape read off the produced LeRobot v3 tree on disk. The
    # in-image sample is the Beta-675 fixture truncated to 20 frames
    # (`app/demo_assets/agibot_sample/`, `scripts/make_demo_sample.py`).
    assert body["dataset"]["episodes"] == 1
    assert body["dataset"]["frames"] == 20
    assert body["dataset"]["codebase_version"] == "v3.0"
    assert body["dataset"]["output_bytes"] > 0
    assert any(f.endswith("meta/info.json") for f in body["dataset"]["file_tree"])

    await engine.dispose()


@pytest.mark.skipif(not _embodied_data_available(), reason="embodied-data CLI not available")
@pytest.mark.asyncio
async def test_demo_emits_funnel_demo_run_row(monkeypatch) -> None:
    """A successful demo writes exactly one anonymous `funnel_events` row
    `kind="demo_run"` (so demo->signup is measurable) and the optional
    `anon_id` query param is honored — no PII, no org."""
    import app.db.session as db_session

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(db_session, "_engine", engine)
    monkeypatch.setattr(db_session, "_sessionmaker", sm)

    import app.main as main_mod

    importlib.reload(main_mod)
    main_mod.app.state.limiter = limiter

    transport = httpx.ASGITransport(app=main_mod.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/api/v1/demo/convert?anon_id=anon-demo-xyz", timeout=120)
    assert r.status_code == 200 and r.json()["ok"] is True

    assert await _count(sm, FunnelEvent) == 1
    async with sm() as s:
        row = (await s.execute(select(FunnelEvent))).scalar_one()
    assert row.kind == "demo_run"
    assert row.anon_id == "anon-demo-xyz"
    assert row.meta.get("ok") is True
    # R3: the demo must NOT touch the org-scoped tables.
    assert await _count(sm, Job) == 0
    assert await _count(sm, UsageEvent) == 0

    await engine.dispose()


# --------------------------------------------------------------------------
# safety-rail tests (no real converter needed — these short-circuit before
# the subprocess or fault it out)
# --------------------------------------------------------------------------


def test_demo_rejects_request_body(db_client) -> None:
    """The input is a fixed server-side sample. Any body/upload must be
    refused — and as a soft 200 {ok:false} (NOT a scary 4xx on Landing) —
    so this never becomes an arbitrary-anon-upload surface. No subprocess
    is spawned for a rejected body."""
    client, sm = db_client
    r = client.post(
        "/api/v1/demo/convert",
        content=b'{"file": "evil.zip"}',
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert "no input" in body["message"].lower()
    # Rejected before any conversion -> no funnel row, no R3 surface.
    import asyncio as _a

    assert _a.run(_count(sm, FunnelEvent)) == 0
    assert _a.run(_count(sm, Job)) == 0


def test_demo_rate_limited_over_5_per_min(monkeypatch, db_client) -> None:
    """5/min/IP cap (strict because each accepted call spawns a real
    subprocess). The 6th from one IP in the same minute -> 429 house
    envelope + Retry-After. We stub the runner so the test doesn't do 5
    real conversions just to prove the limiter wiring."""
    client, _ = db_client

    import app.api.demo as demo_mod

    class _FastRunner:
        async def run(self, sess):  # minimal RunOutcome-shaped success
            from app.api.subprocess_runner import RunOutcome

            (sess.out_dir / "meta").mkdir(parents=True, exist_ok=True)
            (sess.out_dir / "meta" / "info.json").write_text(
                '{"codebase_version":"v3.0","total_episodes":1,"total_frames":1090}'
            )
            return RunOutcome(
                returncode=0,
                signal_killed=None,
                last_stdout_json={"result": "PASS", "results": []},
                stderr_tail="",
                timed_out=False,
            )

    demo_mod.set_state(_FastRunner())  # type: ignore[arg-type]
    monkeypatch.setattr(demo_mod, "_DEMO_FROM", "agibot")

    ip = {"X-Forwarded-For": "203.0.113.77"}
    statuses = [client.post("/api/v1/demo/convert", headers=ip).status_code for _ in range(5)]
    assert statuses.count(200) == 5, f"expected 5x200, got {statuses}"
    over = client.post("/api/v1/demo/convert", headers=ip)
    assert over.status_code == 429
    assert "Retry-After" in over.headers
    assert over.json()["detail"]["code"] == "rate_limit_exceeded"


def test_demo_busy_returns_503_when_locked(db_client) -> None:
    """Single-flight: if a demo conversion is already running the second
    caller gets a clean 503 'busy' (NOT a second spawned subprocess, NOT a
    hang). We hold the process-global lock to simulate an in-flight run."""
    client, _ = db_client
    import app.api.demo as demo_mod

    async def _hold_and_call():
        async with demo_mod._demo_lock:
            # Lock held -> the endpoint must 503 without spawning anything.
            return await asyncio.to_thread(client.post, "/api/v1/demo/convert")

    r = asyncio.run(_hold_and_call())
    assert r.status_code == 503
    body = r.json()
    assert body["ok"] is False
    assert body["state"] == "busy"
    assert "Retry-After" in r.headers


def test_demo_never_500_when_converter_faults(db_client) -> None:
    """A converter crash / internal error must DEGRADE to a clean 200
    {ok:false} so Landing never shows a raw 500 or blank page. We make the
    runner raise and assert the endpoint stays 200 with a soft message and
    no traceback leak."""
    client, sm = db_client
    import app.api.demo as demo_mod

    class _BoomRunner:
        async def run(self, sess):
            raise RuntimeError("simulated converter crash")

    demo_mod.set_state(_BoomRunner())  # type: ignore[arg-type]

    r = client.post("/api/v1/demo/convert")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is False
    assert body["state"] == "error"
    assert "Traceback" not in r.text
    # The failure path still records a funnel row (ok:false) and never an
    # R3-scoped row.
    import asyncio as _a

    assert _a.run(_count(sm, FunnelEvent)) == 1
    assert _a.run(_count(sm, Job)) == 0
    assert _a.run(_count(sm, UsageEvent)) == 0


def test_demo_failed_conversion_degrades_soft(db_client) -> None:
    """If the real converter returns a non-done outcome (e.g. validation
    FAIL), the demo surfaces the wrapper's honest message as a soft 200
    {ok:false} with a sign-up CTA — never a 500, never a stderr leak."""
    client, _sm = db_client
    import app.api.demo as demo_mod

    class _FailRunner:
        async def run(self, sess):
            from app.api.subprocess_runner import RunOutcome

            return RunOutcome(
                returncode=1,
                signal_killed=None,
                last_stdout_json={
                    "result": "FAIL",
                    "results": [{"name": "schema conformance", "status": "FAIL", "detail": "x"}],
                },
                stderr_tail="secret stderr that must not leak",
                timed_out=False,
            )

    demo_mod.set_state(_FailRunner())  # type: ignore[arg-type]

    r = client.post("/api/v1/demo/convert")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert body["state"] == "error"
    assert "secret stderr" not in r.text
    assert "Sign up" in body["suggestion"]
