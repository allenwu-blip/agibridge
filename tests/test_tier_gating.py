"""Story #8 — tier gating unit tests.

Pins `dispatches/D4_specs.md:147-151` + `stripe_webhook_spec.md` §4.3:
- Free tier → `--max-episodes 1` on the embodied-data invocation (preview).
- Soft-cap ladder Free 5 / Solo 50 / Team 200 / Enterprise unlimited
  (D4_specs.md:37, DR-018 #2) → over cap raises SoftCapExceeded (→ 402).
- Cancel-grace: a canceled sub keeps its paid tier until
  current_period_end (§4.3) even though orgs.tier was flipped to free.
- DR-019 invariant: when max_episodes is None the subprocess argv is
  byte-identical to the verified command (the flag is purely additive).

sqlite+aiosqlite in-memory, parity with test_r3_cross_org_isolation.py.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.billing_config import MONTHLY_JOB_SOFT_CAP
from app.api.jobs import _resolve_max_episodes
from app.api.session_store import Session
from app.api.subprocess_runner import SubprocessRunner
from app.api.tier_gating import (
    SoftCapExceeded,
    effective_tier,
    enforce_and_resolve,
    max_episodes_for_tier,
)
from app.db.models import Base, Job, Org, OrgTier, Subscription


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as s:
        yield s
    await engine.dispose()


# ----------------------- episode cap (§ Story #8 free preview) -----------------------


def test_free_tier_caps_episodes_at_one():
    assert max_episodes_for_tier(OrgTier.free) == 1


@pytest.mark.parametrize("tier", [OrgTier.solo, OrgTier.team, OrgTier.enterprise])
def test_paid_tiers_have_no_episode_cap(tier):
    assert max_episodes_for_tier(tier) is None


def test_resolve_max_episodes_free_is_hard_ceiling():
    # free cap = 1, client asks 50 → still 1 (ceiling, not default)
    assert _resolve_max_episodes(1, 50) == 1
    # free cap = 1, client asks nothing → 1
    assert _resolve_max_episodes(1, None) == 1


def test_resolve_max_episodes_paid_honors_client_smoke_run():
    # paid (no cap), client asks 3 (quick smoke) → 3
    assert _resolve_max_episodes(None, 3) == 3
    # paid, no client ask → None (full convert)
    assert _resolve_max_episodes(None, None) is None
    # non-positive client ask is ignored (would convert nothing)
    assert _resolve_max_episodes(None, 0) is None
    assert _resolve_max_episodes(None, -5) is None


# ----------------------- DR-019: additive flag, byte-identical argv -----------------------


def test_subprocess_cmd_unchanged_when_no_episode_cap(tmp_path):
    """When sess.max_episodes is None the argv must NOT contain
    --max-episodes — DR-019 byte-for-byte conversion path preserved."""
    sess = Session(session_id="j1", root=tmp_path)
    sess.from_format, sess.to_format = "agibot", "lerobot-v3"
    sess.max_episodes = None
    cmd = SubprocessRunner()._build_cmd(sess)
    assert "--max-episodes" not in cmd


def test_subprocess_cmd_appends_flag_when_capped(tmp_path):
    sess = Session(session_id="j2", root=tmp_path)
    sess.from_format, sess.to_format = "agibot", "lerobot-v3"
    sess.max_episodes = 1
    cmd = SubprocessRunner()._build_cmd(sess)
    i = cmd.index("--max-episodes")
    assert cmd[i + 1] == "1"
    # flag sits AFTER --verify (on the convert subcommand), not mangling it
    assert cmd.index("--verify") < i


# ----------------------- soft cap ladder (D4_specs.md:37) -----------------------


def test_soft_cap_ladder_matches_dr018():
    assert MONTHLY_JOB_SOFT_CAP[OrgTier.free] == 5
    assert MONTHLY_JOB_SOFT_CAP[OrgTier.solo] == 50
    assert MONTHLY_JOB_SOFT_CAP[OrgTier.team] == 200
    assert MONTHLY_JOB_SOFT_CAP[OrgTier.enterprise] is None  # unlimited


@pytest.mark.asyncio
async def test_under_cap_returns_episode_limit(session):
    org = Org(id="org_F", name="F", tier=OrgTier.free)
    session.add(org)
    await session.flush()
    # 1 job this month (the just-presigned one) — under the free cap of 5
    session.add(Job(org_id="org_F", from_format="agibot", to_format="lerobot-v3"))
    await session.flush()
    max_ep = await enforce_and_resolve(session, org)
    assert max_ep == 1  # free → episode-capped, NOT over soft cap


@pytest.mark.asyncio
async def test_over_soft_cap_raises(session):
    org = Org(id="org_F2", name="F2", tier=OrgTier.free)
    session.add(org)
    await session.flush()
    # free cap = 5; create 6 jobs this month → the 6th trips the cap
    for _ in range(6):
        session.add(Job(org_id="org_F2", from_format="agibot", to_format="lerobot-v3"))
    await session.flush()
    with pytest.raises(SoftCapExceeded) as ei:
        await enforce_and_resolve(session, org)
    assert ei.value.tier == OrgTier.free
    assert ei.value.cap == 5
    assert ei.value.used == 6


@pytest.mark.asyncio
async def test_enterprise_never_soft_capped(session):
    org = Org(id="org_E", name="E", tier=OrgTier.enterprise)
    session.add(org)
    await session.flush()
    for _ in range(500):
        session.add(Job(org_id="org_E", from_format="agibot", to_format="lerobot-v3"))
    await session.flush()
    max_ep = await enforce_and_resolve(session, org)  # must NOT raise
    assert max_ep is None  # enterprise → no episode cap either


# ----------------------- §4.3 cancel-grace window -----------------------


@pytest.mark.asyncio
async def test_canceled_sub_keeps_paid_tier_until_period_end(session):
    """orgs.tier flipped to free by subscription.deleted, but the sub row
    still has current_period_end in the future → effective tier is the
    PAID tier until then (§4.3 — webhook preserves current_period_end)."""
    org = Org(id="org_C", name="C", tier=OrgTier.free)
    session.add(org)
    session.add(
        Subscription(
            id="sub_C",
            org_id="org_C",
            stripe_customer_id="cus_C",
            tier=OrgTier.team,
            status="canceled",
            current_period_end=datetime.now(UTC) + timedelta(days=7),
        )
    )
    await session.flush()
    assert await effective_tier(session, org) == OrgTier.team  # grace honored
    # team cap is 200, not free's 5 — under cap, no episode cap (paid)
    assert await enforce_and_resolve(session, org) is None


@pytest.mark.asyncio
async def test_canceled_sub_past_period_end_is_free(session):
    org = Org(id="org_D", name="D", tier=OrgTier.free)
    session.add(org)
    session.add(
        Subscription(
            id="sub_D",
            org_id="org_D",
            stripe_customer_id="cus_D",
            tier=OrgTier.solo,
            status="canceled",
            current_period_end=datetime.now(UTC) - timedelta(days=1),  # expired
        )
    )
    await session.flush()
    assert await effective_tier(session, org) == OrgTier.free  # grace lapsed
    assert await enforce_and_resolve(session, org) == 1  # back to free preview
