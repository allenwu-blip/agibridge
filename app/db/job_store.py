"""DB-backed JobStore. R3 invariant: every method that touches a job by id
requires (job_id, org_id). There is NO `get_by_id(job_id)` method.

Replaces `app.api.session_store.SessionStore` (session_store.py:94-137) which
was single-tenant. Wiring into handlers lands in D4 alongside Clerk JWT
middleware.

Source: architect tech_spec §6 Risk 3 (cross-org data leak existential).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Job, JobState

_ALLOWED_UPDATE_FIELDS = frozenset(
    {
        "started_at",
        "finished_at",
        "input_uri",
        "output_uri",
        "error_code",
        "error_msg",
        "stderr_tail",
        "estimated_total_s",
    }
)


class JobStore:
    """Per-request store. Holds the AsyncSession; the session lifecycle is
    managed by `app.db.session.get_session` (commit/rollback on exit).
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        org_id: str,
        user_id: str | None,
        from_format: str,
        to_format: str,
    ) -> Job:
        """Insert a new job in `pending` state.

        `org_id` MUST come from the verified JWT claim, NEVER from request
        body. Caller is responsible for trust-boundary enforcement.
        """
        job = Job(
            org_id=org_id,
            user_id=user_id,
            state=JobState.pending,
            from_format=from_format,
            to_format=to_format,
        )
        self._session.add(job)
        await self._session.flush()  # populate job.id
        return job

    async def get(self, *, job_id: uuid.UUID, org_id: str) -> Job | None:
        """Org-scoped read. Returns None if the job does not exist OR if it
        exists in a different org.

        Callers MUST NOT distinguish "not found" from "wrong org" in their
        HTTP response — both 404. Leaking 'this id exists but not for you'
        is itself a side-channel.
        """
        stmt = select(Job).where(Job.id == job_id, Job.org_id == org_id)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def list_for_org(
        self,
        *,
        org_id: str,
        limit: int = 50,
        before: datetime | None = None,
    ) -> Sequence[Job]:
        """Org-scoped listing, most-recent-first. `before` cursor for
        keyset pagination on `created_at`.

        There is no cross-org variant; this is the only listing method.
        """
        stmt = select(Job).where(Job.org_id == org_id).order_by(Job.created_at.desc()).limit(limit)
        if before is not None:
            stmt = stmt.where(Job.created_at < before)
        return (await self._session.execute(stmt)).scalars().all()

    async def update_state(
        self,
        *,
        job_id: uuid.UUID,
        org_id: str,
        state: JobState,
        **fields: Any,
    ) -> bool:
        """Atomic state update, org-scoped. Returns True if a row was
        updated, False if `(job_id, org_id)` did not match. Caller raises
        404 on False.

        Allowed `fields`: started_at, finished_at, input_uri, output_uri,
        error_code, error_msg, stderr_tail, estimated_total_s. Unknown keys
        raise ValueError to fail loudly at dev time rather than silently
        dropping data.
        """
        bad = set(fields) - _ALLOWED_UPDATE_FIELDS
        if bad:
            raise ValueError(f"unexpected field(s): {sorted(bad)}")
        stmt = (
            update(Job).where(Job.id == job_id, Job.org_id == org_id).values(state=state, **fields)
        )
        result = await self._session.execute(stmt)
        return (result.rowcount or 0) > 0
