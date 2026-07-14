"""Alembic environment. Reads DATABASE_URL from env (Neon connection string).

Sync engine is used for migrations (Alembic doesn't need async). Models are
imported from app.db.models so target_metadata is populated for autogenerate.
"""

from __future__ import annotations

import os
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context
from app.db.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _db_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL env var required for alembic")
    # asyncpg driver is for runtime; alembic uses sync psycopg.
    return (
        url.replace("+asyncpg", "")
        .replace("postgresql+", "postgresql+psycopg://")
        .replace("postgresql+psycopg://psycopg://", "postgresql+psycopg://")
        if "+asyncpg" in url
        else url
    )


def run_migrations_offline() -> None:
    context.configure(
        url=_db_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section) or {}
    section["sqlalchemy.url"] = _db_url()
    connectable = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
