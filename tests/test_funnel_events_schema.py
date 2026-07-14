"""Schema + migration guard for the funnel instrumentation surface.

Asserts:
  1. `FunnelEvent` ORM model shape: exactly the minimal non-PII columns,
     no FK (so it is org-less by construction and R3 is untouched).
  2. `FunnelEvent.meta` uses the SAME portable JSON variant as
     `UsageEvent.meta` (JSON on sqlite / JSONB on Postgres) so tests'
     `Base.metadata.create_all` on sqlite works while prod gets JSONB.
  3. The Alembic revision graph is a single head, chained `0001 -> 0002`.
  4. The `0002` migration's UPGRADE compiles against the POSTGRES dialect
     and emits NO `CREATE TYPE` (the DR-021 enum-double-create trap does
     NOT apply — `funnel_events` has no enum) and DOES emit the JSONB
     column. (Real-Neon apply is the orchestrator's pre-merge gate per
     DR-021: CI builds schema via create_all on sqlite, so this offline
     DDL compile is the strongest local proof.)

No DB connection is made (offline `--sql`-equivalent via Alembic's
`MigrationContext` in offline mode), mirroring the network-pure posture of
`tests/test_clerk_jwt.py`.
"""

from __future__ import annotations

import io
from contextlib import redirect_stdout
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy.dialects.postgresql import JSONB

from alembic import command
from app.db.models import FunnelEvent, UsageEvent

_REPO = Path(__file__).resolve().parent.parent


def _alembic_cfg() -> Config:
    cfg = Config(str(_REPO / "alembic.ini"))
    cfg.set_main_option("script_location", str(_REPO / "alembic"))
    return cfg


def test_funnel_event_model_is_minimal_and_pii_free() -> None:
    cols = {c.name: c for c in FunnelEvent.__table__.columns}
    assert set(cols) == {"id", "anon_id", "kind", "meta", "occurred_at"}
    # No foreign keys → never org-scoped → R3 invariant untouched.
    assert len(FunnelEvent.__table__.foreign_keys) == 0
    assert not cols["anon_id"].nullable
    assert not cols["kind"].nullable
    assert not cols["meta"].nullable
    assert cols["id"].primary_key


def test_funnel_meta_uses_same_portable_json_as_usage_events() -> None:
    """Both must render JSONB on Postgres so prod is consistent, and JSON
    on sqlite so `Base.metadata.create_all` works in CI (the variant
    posture of `app/db/models.py:_json_type`)."""
    funnel_pg = FunnelEvent.__table__.c.meta.type.dialect_impl(
        __import__("sqlalchemy.dialects.postgresql", fromlist=["dialect"]).dialect()
    )
    usage_pg = UsageEvent.__table__.c.meta.type.dialect_impl(
        __import__("sqlalchemy.dialects.postgresql", fromlist=["dialect"]).dialect()
    )
    assert isinstance(funnel_pg, JSONB)
    assert type(funnel_pg) is type(usage_pg)


def test_revision_graph_single_head_chained() -> None:
    script = ScriptDirectory.from_config(_alembic_cfg())
    heads = list(script.get_heads())
    assert heads == ["0002"], f"expected single head 0002, got {heads}"
    rev_0002 = script.get_revision("0002")
    assert rev_0002.down_revision == "0001"
    rev_0001 = script.get_revision("0001")
    assert rev_0001.down_revision is None  # base


def test_0002_upgrade_postgres_ddl_has_jsonb_and_no_create_type(monkeypatch) -> None:
    """Render the 0001->0002 upgrade as OFFLINE Postgres SQL (no DB
    connection — `alembic upgrade 0001:0002 --sql`, the exact verification
    the brief requires) and assert the DR-021 lesson holds: a CREATE TABLE
    with a JSONB meta column, the expected index, and crucially NO
    `CREATE TYPE` (no enum here, so the
    `postgresql.ENUM(create_type=False)` double-create bug cannot recur).

    Real-Neon apply remains the orchestrator's pre-merge gate (DR-021: CI
    builds schema via `Base.metadata.create_all` on sqlite, so the
    Postgres-only DDL path is only fully proven on a real Postgres). This
    offline compile is the strongest local proof — it exercises the
    Postgres dialect compiler on the actual migration ops."""
    # env.py reads DATABASE_URL; the offline `--sql` path never connects, it
    # only needs a parseable Postgres URL to pick the dialect compiler.
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@localhost/db")

    buf = io.StringIO()
    with redirect_stdout(buf):
        command.upgrade(_alembic_cfg(), "0001:0002", sql=True)
    sql = buf.getvalue()

    assert "CREATE TABLE funnel_events" in sql
    assert "JSONB" in sql
    assert "ix_funnel_events_kind_occurred_at" in sql
    # The DR-021 trap (carried from 0001_initial.py:26-34): a migration that
    # creates a PG ENUM double-emits CREATE TYPE. funnel_events has no enum;
    # assert we did not introduce one.
    assert "CREATE TYPE" not in sql.upper()
    # The version row must chain 0001 -> 0002 (not a branch/duplicate head).
    assert "version_num='0002'" in sql


def test_0002_downgrade_is_clean_reversible(monkeypatch) -> None:
    """The offline 0002->0001 downgrade drops exactly the table+index it
    created and rewinds the version pointer — reversible, no orphan DDL."""
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@localhost/db")
    buf = io.StringIO()
    with redirect_stdout(buf):
        command.downgrade(_alembic_cfg(), "0002:0001", sql=True)
    sql = buf.getvalue()
    assert "DROP TABLE funnel_events" in sql
    assert "DROP INDEX ix_funnel_events_kind_occurred_at" in sql
    assert "version_num='0001'" in sql
