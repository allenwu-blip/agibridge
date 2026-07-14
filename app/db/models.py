"""SQLAlchemy 2.0 declarative models for agibridge commercial cut.

Tables: orgs, users, subscriptions, jobs, usage_events, stripe_events.

Design notes:
- `Org.id` and `User.id` are TEXT, NOT UUID. They hold Clerk's string ids
  verbatim (`org_xxx`, `user_xxx`) so JWT claims pass through with zero
  translation. Source: architect tech_spec §2.2 (Clerk B2B Orgs).
- `Subscription.id` is Stripe's `sub_xxx` string (Stripe is system-of-record).
- `Job.id` is UUID (server-generated). Avoids ULID coupling with the F-1
  session_id and is opaque to clients.
- `stripe_events.id` is the webhook event_id, PRIMARY KEY for idempotency
  per architect tech_spec §6 Risk 2.

R3 enforcement is at app layer (see `job_store.py`); the composite index
on (org_id, id) makes the org-scoped read index-only.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import CHAR, JSON, TypeDecorator

# --- portable UUID / JSON for SQLite-in-tests + Postgres-in-prod ---


class GUID(TypeDecorator):
    """Platform-independent UUID. Uses Postgres UUID when available, otherwise
    CHAR(36). Lets us run R3 isolation tests on sqlite+aiosqlite without
    needing a Neon test branch token.
    """

    impl = CHAR
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(PG_UUID(as_uuid=True))
        return dialect.type_descriptor(CHAR(36))

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if dialect.name == "postgresql":
            return value
        return str(value)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if isinstance(value, uuid.UUID):
            return value
        return uuid.UUID(value)


def _json_type():
    """JSONB on Postgres, JSON on sqlite. Both round-trip dicts."""
    return JSON().with_variant(JSONB(), "postgresql")


# --- enums ---


class JobState(str, Enum):
    """Mirrors app.schemas.State (schemas.py:25-39) for v0 — kept as a separate
    enum so DB migrations are decoupled from API schema churn.
    """

    pending = "pending"
    extracting = "extracting"
    running = "running"
    validating = "validating"
    done = "done"
    failed = "failed"
    expired = "expired"


class OrgTier(str, Enum):
    """Pricing tiers per CLAUDE.md (DR-003)."""

    free = "free"  # signed-up, no Stripe sub
    solo = "solo"  # $50/mo
    team = "team"  # $250/mo
    enterprise = "enterprise"  # $1000/mo


# --- base ---


class Base(DeclarativeBase):
    pass


# --- tables ---


class Org(Base):
    """Mirror of Clerk Organization. PK = Clerk's `org_xxx` string."""

    __tablename__ = "orgs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    tier: Mapped[OrgTier] = mapped_column(
        SAEnum(OrgTier, name="org_tier"),
        default=OrgTier.free,
        nullable=False,
    )
    stripe_customer_id: Mapped[str | None] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    users: Mapped[list[User]] = relationship(back_populates="org")
    jobs: Mapped[list[Job]] = relationship(back_populates="org")


class User(Base):
    """Mirror of Clerk User. PK = Clerk's `user_xxx` string.

    `org_id` nullable: DB-level a user MAY belong to no org so Clerk-sync is
    idempotent during the brief window between user-create and org-join. App
    layer requires org_id non-null for any job operation.
    """

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    org_id: Mapped[str | None] = mapped_column(
        ForeignKey("orgs.id", ondelete="SET NULL"),
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    org: Mapped[Org | None] = relationship(back_populates="users")

    __table_args__ = (Index("ix_users_email", "email"),)


class Subscription(Base):
    """Mirror of Stripe Subscription. Webhook (Story #7) keeps this in sync.

    `current_period_end` drives tier-gating cutoff (Story #8). Stored
    server-side so a stale JWT can't extend access past Stripe's truth.
    """

    __tablename__ = "subscriptions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # sub_xxx
    org_id: Mapped[str] = mapped_column(
        ForeignKey("orgs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    stripe_customer_id: Mapped[str] = mapped_column(String(64), nullable=False)
    tier: Mapped[OrgTier] = mapped_column(SAEnum(OrgTier, name="org_tier"), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    current_period_end: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class Job(Base):
    """A single conversion run. Replaces ephemeral Session (session_store.py:35-91).

    `input_uri` / `output_uri` are R2 keys (not URLs); presign-on-demand keeps
    rotation cheap.
    """

    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        primary_key=True,
        default=uuid.uuid4,
    )
    org_id: Mapped[str] = mapped_column(
        ForeignKey("orgs.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
    )
    state: Mapped[JobState] = mapped_column(
        SAEnum(JobState, name="job_state"),
        default=JobState.pending,
        nullable=False,
    )
    from_format: Mapped[str | None] = mapped_column(String(32))
    to_format: Mapped[str | None] = mapped_column(String(32))
    input_uri: Mapped[str | None] = mapped_column(String(512))  # R2 key
    output_uri: Mapped[str | None] = mapped_column(String(512))  # R2 key
    estimated_total_s: Mapped[int | None] = mapped_column()
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_msg: Mapped[str | None] = mapped_column(Text)
    stderr_tail: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    org: Mapped[Org] = relationship(back_populates="jobs")

    __table_args__ = (
        # R3: composite index lets WHERE org_id=? AND id=? be index-only.
        Index("ix_jobs_org_id_id", "org_id", "id"),
        # Dashboard listing: org-scoped, most-recent-first.
        Index("ix_jobs_org_id_created_at", "org_id", "created_at"),
    )


class UsageEvent(Base):
    """Append-only metering log. Drives tier-cap enforcement (Story #8) and
    future usage-based pricing.
    """

    __tablename__ = "usage_events"

    # BigInteger on Postgres (prod); plain Integer on SQLite so the
    # `INTEGER PRIMARY KEY` rowid-autoincrement fires (SQLite does NOT
    # autoincrement a BIGINT PK — Story #7 invoice.paid/payment_failed
    # writes are the first test that exercises this table). Same
    # portable-variant posture as `_json_type()` (models.py:74-76).
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer(), "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    org_id: Mapped[str] = mapped_column(
        ForeignKey("orgs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(),
        ForeignKey("jobs.id", ondelete="SET NULL"),
    )
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    meta: Mapped[dict] = mapped_column(_json_type(), default=dict, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    __table_args__ = (Index("ix_usage_org_id_occurred_at", "org_id", "occurred_at"),)


class FunnelEvent(Base):
    """Append-only ANONYMOUS / pre-auth funnel log.

    Why a separate table and not `usage_events`: `usage_events.org_id` is
    NOT NULL with an FK to `orgs.id` (`models.py:270-274` /
    `alembic/versions/0001_initial.py:180-185`). The top-of-funnel events
    (`landing_view`, `pricing_view`, `dashboard_view`, `signup_started`,
    `checkout_started`) are emitted by the browser BEFORE an org exists (or
    on a fire-and-forget path we deliberately do not org-trust), so they
    have no `org_id`. Weakening the `usage_events` NOT-NULL/FK to admit them
    would erode R3's org-scoping invariant. Keeping a minimal sibling table
    preserves R3 untouched.

    Privacy: `anon_id` is a CLIENT-generated random UUID persisted in the
    browser's localStorage — NOT a fingerprint, NOT PII, NOT an IP, NOT an
    email. It only correlates events within one browser for funnel-rate
    math. `meta` is a small non-PII JSON blob (e.g. `{"tier": "solo"}`),
    validated server-side at the `/api/v1/events` edge.

    Server-trustworthy conversions (`signup_completed`,
    `checkout_completed`) do NOT live here — they are org-scoped and emitted
    server-side into `usage_events` inside the existing webhook transactions
    (`app/api/webhooks.py`, `app/api/billing.py`).
    """

    __tablename__ = "funnel_events"

    # Same portable BigInteger/Integer variant posture as `usage_events.id`
    # (models.py:265-269): SQLite needs the INTEGER PRIMARY KEY rowid path to
    # autoincrement; Postgres (Neon, prod) gets BIGINT.
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer(), "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    anon_id: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    meta: Mapped[dict] = mapped_column(_json_type(), default=dict, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    __table_args__ = (
        # Funnel analytics query shape: by kind over a time window.
        Index("ix_funnel_events_kind_occurred_at", "kind", "occurred_at"),
    )


class StripeEvent(Base):
    """Webhook idempotency table.

    Webhook handler does `INSERT ... ON CONFLICT DO NOTHING`; retries become
    no-ops. Per architect tech_spec §6 Risk 2 mitigation.
    """

    __tablename__ = "stripe_events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # evt_xxx
    type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict] = mapped_column(_json_type(), nullable=False)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
