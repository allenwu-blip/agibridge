"""PurgeReaper invariants. Spec §4 HP-3 + freezegun clock advance."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from freezegun import freeze_time

from app.api.session_store import SessionStore
from app.purge_reaper import PurgeReaper
from app.schemas import State


async def test_expired_session_purged() -> None:
    store = SessionStore()
    sess = await store.create("01HX_purge_target_aaa")
    # Force expiration into the past.
    sess.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    sess.write_status()

    assert sess.root.is_dir()
    reaper = PurgeReaper(store, interval_s=60)
    purged = await reaper.purge_once()
    assert purged == 1
    assert not sess.root.exists()
    assert await store.get(sess.session_id) is None


async def test_active_session_not_purged() -> None:
    store = SessionStore()
    sess = await store.create("01HX_keep_alive_xxxx")
    # expires_at is now+30min by default; should not be purged.
    reaper = PurgeReaper(store, interval_s=60)
    purged = await reaper.purge_once()
    assert purged == 0
    assert sess.root.is_dir()


async def test_freezegun_advance_triggers_purge() -> None:
    """Spec §4 step 4 + brief DoD #6: stale via clock advance."""
    store = SessionStore()
    with freeze_time("2026-05-02 14:00:00") as frozen:
        sess = await store.create("01HX_freeze_target_x")
        # expires_at = now + 30 min by SessionStore default.
        assert sess.root.is_dir()
        # Advance past the 30-min purge window.
        frozen.tick(delta=timedelta(minutes=31))
        reaper = PurgeReaper(store, interval_s=60)
        purged = await reaper.purge_once()
        assert purged == 1
        assert not sess.root.exists()


async def test_hard_60_min_cap_purges_even_running_session() -> None:
    """Spec §4: hard rule, never keep a session > 60 min even if state==running.

    Defense-in-depth for a stuck reaper."""
    store = SessionStore()
    with freeze_time("2026-05-02 14:00:00") as frozen:
        sess = await store.create("01HX_stuck_run_aaaa")
        sess.state = State.running
        # Pretend the reaper bug never updated expires_at — sess looks
        # 'forever active'. Hard cap is 60 min from created_at.
        sess.expires_at = datetime.now(UTC) + timedelta(hours=10)
        frozen.tick(delta=timedelta(minutes=61))
        reaper = PurgeReaper(store, interval_s=60)
        purged = await reaper.purge_once()
        assert purged == 1


async def test_open_fd_survives_rmtree(tmp_path: Path) -> None:
    """Spec §4 HP-3: in-flight StreamingResponse survives via open FD.

    On Linux/macOS the inode is released only after the last FD closes.
    `shutil.rmtree` removes the directory entry but the open fd keeps reading."""
    import shutil

    target_dir = tmp_path / "session"
    target_dir.mkdir()
    fpath = target_dir / "result.zip"
    fpath.write_bytes(b"hello-world" * 1000)
    expected = fpath.read_bytes()

    # Open BEFORE rmtree (matches download.py order).
    fh = fpath.open("rb")
    shutil.rmtree(target_dir, ignore_errors=True)
    assert not target_dir.exists()
    # FD is still readable.
    got = fh.read()
    fh.close()
    assert got == expected
