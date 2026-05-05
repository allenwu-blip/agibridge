"""PurgeReaper — async background task. Spec §4 step 4.

Runs every 60 s, removes expired sessions. In-flight StreamingResponse holding
an open FD survives `shutil.rmtree` because Linux keeps the inode alive while
any FD references it (HP-3, spec §4 step 3).
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from app.api.session_store import HARD_PURGE_TIMEOUT, Session

if TYPE_CHECKING:
    from app.api.session_store import SessionStore

PURGE_INTERVAL_S = 60

logger = logging.getLogger("agibridge.purge")


class PurgeReaper:
    def __init__(self, store: SessionStore, interval_s: int = PURGE_INTERVAL_S) -> None:
        self._store = store
        self._interval_s = interval_s
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None

    async def _loop(self) -> None:
        try:
            while not self._stop_event.is_set():
                try:
                    await self.purge_once()
                except Exception:
                    # Reaper must not die; log and keep ticking.
                    logger.exception("purge tick failed")
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=self._interval_s)
                except TimeoutError:
                    continue
        except asyncio.CancelledError:
            return

    async def purge_once(self) -> int:
        """Remove all expired sessions. Returns count purged. Public for tests."""
        now = datetime.now(UTC)
        sessions = await self._store.list_all()
        purged = 0
        for sess in sessions:
            if self._should_purge(sess, now):
                await asyncio.to_thread(_rmtree_quietly, sess)
                await self._store.remove(sess.session_id)
                purged += 1
        return purged

    @staticmethod
    def _should_purge(sess: Session, now: datetime) -> bool:
        # Hard cap (spec §4): never keep > 60 min, even if state == running.
        hard_cap = sess.created_at + HARD_PURGE_TIMEOUT
        if now >= hard_cap:
            return True
        return now >= sess.expires_at


def _rmtree_quietly(sess: Session) -> None:
    shutil.rmtree(sess.root, ignore_errors=True)
