"""Async SQLAlchemy session factory.

Reads `DATABASE_URL` (Neon connection string in prod, sqlite+aiosqlite in
tests). Module-level engine is created lazily so import-time doesn't try
to connect during test collection or alembic offline mode.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def _build_engine() -> AsyncEngine:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL not set. Set to Neon postgres+asyncpg://... in prod, "
            "or sqlite+aiosqlite:///:memory: in tests."
        )
    return create_async_engine(url, pool_pre_ping=True)


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        _engine = _build_engine()
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(
            get_engine(),
            expire_on_commit=False,
            class_=AsyncSession,
        )
    return _sessionmaker


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency. Wires per-request session w/ commit-on-success,
    rollback-on-error.
    """
    Session = get_sessionmaker()
    async with Session() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
