"""Story #8 — tier gating.

Two enforcement points, both keyed by `orgs.tier` (server-trusted state set
by the Stripe webhook, `app/api/billing.py:_apply_subscription_state`) and
read with the org_id that came ONLY from the verified Clerk JWT (R3,
`app/api/jobs.py:13-18`):

1. **Episode cap** — Free tier converts a 1-episode PREVIEW
   (`dispatches/D4_specs.md:149`, `embodied-data convert --max-episodes 1`).
   Paid tiers: full conversion (no cap).
2. **Monthly soft cap** — conversion jobs/mo per the Cycle-J ladder
   (`dispatches/D4_specs.md:37`, DR-018 #2): Free 5 / Solo 50 / Team 200 /
   Enterprise unlimited. Over cap -> 402 Payment Required.

Tier resolution honors the cancel-grace window (§4.3 of
`app/api/stripe_webhook_spec.md`): a canceled sub keeps its paid tier until
`subscriptions.current_period_end`, so a stale `orgs.tier=free` written by
`subscription.deleted` does not prematurely revoke access the customer paid
for. The webhook sets `orgs.tier=free` on cancel but PRESERVES
`current_period_end` (`models.py:183-186`) precisely so this read can
restore the effective tier.

This module is the gate DECISION (pure-ish: one count query). It wraps the
DR-019 conversion call, it does not alter it.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.billing_config import (
    FREE_TIER_MAX_EPISODES,
    MAX_UPLOAD_BYTES,
    MONTHLY_JOB_SOFT_CAP,
)
from app.db.models import Job, Org, OrgTier, Subscription


def _month_start(now: datetime) -> datetime:
    """First instant of the current UTC calendar month. Soft cap is a
    per-calendar-month count (matches the monthly billing period framing in
    `dispatches/D4_specs.md:37`)."""
    return datetime(now.year, now.month, 1, tzinfo=UTC)


async def effective_tier(db: AsyncSession, org: Org) -> OrgTier:
    """The tier the org is ENTITLED to right now.

    If `orgs.tier` is free but an active/canceled `subscriptions` row still
    has `current_period_end > now()`, the customer paid through that date —
    honor the subscription's tier until then (§4.3). Otherwise `orgs.tier`.
    """
    if org.tier != OrgTier.free:
        return org.tier
    now = datetime.now(UTC)
    sub = (
        await db.execute(
            select(Subscription)
            .where(Subscription.org_id == org.id)
            .order_by(Subscription.current_period_end.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if sub is not None and sub.tier != OrgTier.free:
        cpe = sub.current_period_end
        if cpe.tzinfo is None:
            cpe = cpe.replace(tzinfo=UTC)
        if cpe > now:
            return sub.tier
    return OrgTier.free


def max_episodes_for_tier(tier: OrgTier) -> int | None:
    """Free -> 1-episode preview cap; paid -> None (no cap, full convert)."""
    if tier == OrgTier.free:
        return FREE_TIER_MAX_EPISODES
    return None


def max_upload_bytes_for_tier(tier: OrgTier) -> int | None:
    """The largest archive (compressed, in bytes) this tier may upload.

    `None` == uncapped (Enterprise). Used by `presign_upload` to reject an
    oversized file BEFORE the R2 presigned URL is issued — the upload never
    happens, so there is no silent R2 failure and nothing to clean up.
    """
    return MAX_UPLOAD_BYTES.get(tier)


async def monthly_job_count(db: AsyncSession, org_id: str) -> int:
    """Conversion jobs this org created in the current UTC calendar month.

    Counts `jobs` rows by `created_at` (every job row == one conversion
    attempt; `presign_upload` creates the row, `POST /jobs` is the gate).
    Org-scoped — never cross-org (R3)."""
    since = _month_start(datetime.now(UTC))
    count = (
        await db.execute(
            select(func.count())
            .select_from(Job)
            .where(Job.org_id == org_id, Job.created_at >= since)
        )
    ).scalar_one()
    return int(count or 0)


class SoftCapExceeded(Exception):
    """Raised when the org is at/over its monthly conversion soft cap.
    Caller maps to HTTP 402 + upgrade-prompt email."""

    def __init__(self, tier: OrgTier, used: int, cap: int) -> None:
        self.tier = tier
        self.used = used
        self.cap = cap
        super().__init__(f"{tier.value} soft cap reached: {used}/{cap} this month")


async def enforce_and_resolve(db: AsyncSession, org: Org) -> int | None:
    """Run BOTH gates. Returns the effective `max_episodes` (None == no cap)
    to thread into the conversion. Raises `SoftCapExceeded` if the monthly
    cap is hit (Enterprise == unlimited == never raises).

    Called from `POST /api/v1/jobs` BEFORE the conversion is kicked off. The
    job row already exists (created at presign-upload); the cap counts the
    EXISTING row too, so the Nth job — the one that would exceed the cap —
    is rejected before the subprocess starts (work not done == not billed).
    """
    tier = await effective_tier(db, org)
    cap = MONTHLY_JOB_SOFT_CAP.get(tier)
    if cap is not None:
        used = await monthly_job_count(db, org.id)
        if used > cap:
            raise SoftCapExceeded(tier=tier, used=used, cap=cap)
    return max_episodes_for_tier(tier)
