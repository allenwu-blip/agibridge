"""Initial schema: orgs, users, subscriptions, jobs, usage_events, stripe_events.

Revision ID: 0001
Revises:
Create Date: 2026-05-13

Source: backend-dev-D1-D3.md §2 (architect tech_spec.md story #3).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ENUM as PGEnum
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# create_type=False: the migration owns the PG ENUM lifecycle explicitly via
# the .create(checkfirst=True) / .drop(checkfirst=True) calls below. Without
# this flag, op.create_table()'s implicit table-create DDL path re-emits
# `CREATE TYPE org_tier ...` with checkfirst=False AFTER the explicit create,
# raising psycopg.errors.DuplicateObject on Postgres. CI never caught this:
# tests build schema via Base.metadata.create_all on sqlite-in-memory (see
# tests/conftest.py:101), where sa.Enum degrades to VARCHAR+CHECK and no
# CREATE TYPE is emitted — the Postgres-only double-create path was never
# exercised until the first real Neon deploy (D4 close).
_org_tier = PGEnum(
    "free",
    "solo",
    "team",
    "enterprise",
    name="org_tier",
    create_type=False,
)
_job_state = PGEnum(
    "pending",
    "extracting",
    "running",
    "validating",
    "done",
    "failed",
    "expired",
    name="job_state",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    _org_tier.create(bind, checkfirst=True)
    _job_state.create(bind, checkfirst=True)

    op.create_table(
        "orgs",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column(
            "tier",
            _org_tier,
            nullable=False,
            server_default="free",
        ),
        sa.Column("stripe_customer_id", sa.String(64), unique=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    op.create_table(
        "users",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column(
            "org_id",
            sa.String(64),
            sa.ForeignKey("orgs.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_users_email", "users", ["email"])
    op.create_index("ix_users_org_id", "users", ["org_id"])

    op.create_table(
        "subscriptions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "org_id",
            sa.String(64),
            sa.ForeignKey("orgs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("stripe_customer_id", sa.String(64), nullable=False),
        sa.Column("tier", _org_tier, nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column(
            "current_period_end",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_subscriptions_org_id", "subscriptions", ["org_id"])

    op.create_table(
        "jobs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "org_id",
            sa.String(64),
            sa.ForeignKey("orgs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.String(64),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "state",
            _job_state,
            nullable=False,
            server_default="pending",
        ),
        sa.Column("from_format", sa.String(32)),
        sa.Column("to_format", sa.String(32)),
        sa.Column("input_uri", sa.String(512)),
        sa.Column("output_uri", sa.String(512)),
        sa.Column("estimated_total_s", sa.Integer),
        sa.Column("error_code", sa.String(64)),
        sa.Column("error_msg", sa.Text),
        sa.Column("stderr_tail", sa.Text),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    # R3 critical: composite indexes for (org_id, id) and (org_id, created_at).
    op.create_index("ix_jobs_org_id_id", "jobs", ["org_id", "id"])
    op.create_index(
        "ix_jobs_org_id_created_at",
        "jobs",
        ["org_id", "created_at"],
    )

    op.create_table(
        "usage_events",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "org_id",
            sa.String(64),
            sa.ForeignKey("orgs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "job_id",
            UUID(as_uuid=True),
            sa.ForeignKey("jobs.id", ondelete="SET NULL"),
        ),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column(
            "meta",
            JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_usage_events_org_id", "usage_events", ["org_id"])
    op.create_index(
        "ix_usage_org_id_occurred_at",
        "usage_events",
        ["org_id", "occurred_at"],
    )

    op.create_table(
        "stripe_events",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("type", sa.String(64), nullable=False),
        sa.Column("payload", JSONB, nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True)),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("stripe_events")
    op.drop_index("ix_usage_org_id_occurred_at", table_name="usage_events")
    op.drop_index("ix_usage_events_org_id", table_name="usage_events")
    op.drop_table("usage_events")
    op.drop_index("ix_jobs_org_id_created_at", table_name="jobs")
    op.drop_index("ix_jobs_org_id_id", table_name="jobs")
    op.drop_table("jobs")
    op.drop_index("ix_subscriptions_org_id", table_name="subscriptions")
    op.drop_table("subscriptions")
    op.drop_index("ix_users_org_id", table_name="users")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")
    op.drop_table("orgs")
    bind = op.get_bind()
    _job_state.drop(bind, checkfirst=True)
    _org_tier.drop(bind, checkfirst=True)
