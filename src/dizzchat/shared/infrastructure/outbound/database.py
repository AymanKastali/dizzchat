"""Shared SQLAlchemy base and async engine/session plumbing.

Every bounded context maps its persistence models onto this ``Base`` so a single
``Base.metadata`` drives Alembic. The engine and session factory are created once at startup
(see the app lifespan) and shared across requests.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Declarative base shared across all bounded contexts."""


def create_engine(database_url: str) -> AsyncEngine:
    """Create the async engine for the given asyncpg URL."""
    return create_async_engine(database_url, pool_pre_ping=True)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Create a session factory that yields ``AsyncSession``s bound to ``engine``."""
    return async_sessionmaker(engine, expire_on_commit=False)
