"""Funnel instrumentation: anonymous pre-auth funnel_events table.

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-17

Adds `funnel_events` — the append-only, NON-PII, anonymous top-of-funnel
log (`app/db/models.py` `FunnelEvent`). Server-trustworthy conversion
events (`signup_completed`, `checkout_completed`) reuse the existing
`usage_events` table (org-scoped) and need NO schema change; only the
anonymous/client funnel events need this new sibling table because
`usage_events.org_id` is NOT NULL + FK `orgs.id`
(`alembic/versions/0001_initial.py:180-185`) and the top-of-funnel has no
org. Source: brief "usage_events-vs-funnel_events split".

DR-021 lesson (carried from `0001_initial.py:26-34`): the
`postgresql.ENUM(create_type=False)` double-CREATE-TYPE bug only bites
migrations that create a PG ENUM. `funnel_events` has NO enum column
(`kind` is a plain `String(64)`), so that trap does not apply here — we
keep this migration deliberately enum-free, simple, and idempotent.
Idempotency: `create_table`/`create_index` would raise on a re-run, but
Alembic's revision table gates re-execution; we additionally avoid any
implicit type-create path. Orchestrator verifies against the real Neon
branch in the pre-merge gate (DR-021 posture: CI runs sqlite-in-memory via
`Base.metadata.create_all`, so the Postgres-only DDL path is only proven on
a real Postgres — see PR body).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "funnel_events",
        # BigInteger PK on Postgres; `app/db/models.py:FunnelEvent` carries
        # the .with_variant(Integer(), "sqlite") so tests' sqlite rowid
        # autoincrement fires — that variant is a model-layer concern, the
        # migration only runs against Postgres (Neon) so BigInteger is right.
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("anon_id", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(64), nullable=False),
        # JSONB on Postgres, matching `usage_events.meta`
        # (`0001_initial.py:192-197`) including the `'{}'::jsonb`
        # server_default so an omitted meta is a non-null empty object.
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
    op.create_index(
        "ix_funnel_events_kind_occurred_at",
        "funnel_events",
        ["kind", "occurred_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_funnel_events_kind_occurred_at", table_name="funnel_events")
    op.drop_table("funnel_events")
