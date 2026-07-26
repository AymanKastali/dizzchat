"""Shared FastAPI dependencies reused across bounded contexts (request-scoped session, clock)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from dizzchat.shared.application import Clock
from dizzchat.shared.infrastructure.outbound import SystemClock


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """Yield a request-scoped session and roll it back on error.

    The commit deliberately does **not** happen here. This function's teardown runs on the request
    exit stack, which unwinds only after the response has been sent, so committing here would
    acknowledge a write before it was durable. ``TransactionalRoute`` commits instead, in the window
    before the response goes out; the session is published on ``request.state`` for it to find.
    """
    session_factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    async with session_factory() as session:
        request.state.db_session = session
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


def get_clock() -> Clock:
    """Return the process wall clock."""
    return SystemClock()


SessionDep = Annotated[AsyncSession, Depends(get_session)]
ClockDep = Annotated[Clock, Depends(get_clock)]
