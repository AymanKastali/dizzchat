"""Tests that the request-scoped transaction closes *before* the response reaches the client.

This is an ordering guarantee, not a behaviour, so the tests assert on the raw ASGI event stream: a
spy wrapped around ``send`` records when the response goes out, and a recording session records when
it is committed or rolled back. The interleaving of those two records is the whole point.

Without ``TransactionalRoute`` the order is ``http.response.start`` → ``http.response.body`` →
``commit``, which means a client that immediately reads what it just wrote can be served a snapshot
that does not contain it. That was a real, intermittent 401 on sign-up-then-log-in.
"""

from collections.abc import AsyncIterator
from typing import Any

import pytest
from fastapi import APIRouter, FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from starlette.types import Receive, Scope, Send

from dizzchat.app import create_app
from dizzchat.shared.infrastructure.inbound.api.dependencies import SessionDep
from dizzchat.shared.infrastructure.inbound.api.transactional_route import (
    TransactionalRoute,
    assert_session_routes_are_transactional,
)


class RecordingSession:
    """Stands in for ``AsyncSession``, appending each lifecycle call to a shared event log."""

    def __init__(self, events: list[str]) -> None:
        self._events = events

    async def commit(self) -> None:
        self._events.append("commit")

    async def rollback(self) -> None:
        self._events.append("rollback")


class RecordingSessionFactory:
    """An ``async_sessionmaker`` stand-in: calling it yields an async context manager."""

    def __init__(self, events: list[str]) -> None:
        self._events = events

    def __call__(self) -> "RecordingSessionFactory":
        return self

    async def __aenter__(self) -> RecordingSession:
        return RecordingSession(self._events)

    async def __aexit__(self, *exc_info: object) -> None:
        return None


@pytest.fixture
def events() -> list[str]:
    return []


@pytest.fixture
async def client(events: list[str]) -> AsyncIterator[AsyncClient]:
    """A client for a minimal app that uses the real ``get_session`` and ``TransactionalRoute``.

    ``send`` is wrapped so the response events land in the same log as the session's, which is what
    makes the ordering assertable.
    """
    router = APIRouter(route_class=TransactionalRoute)

    @router.post("/write")
    async def write(session: SessionDep) -> dict[str, str]:
        return {"status": "written"}

    @router.post("/fail")
    async def fail(session: SessionDep) -> dict[str, str]:
        raise HTTPException(status_code=400, detail="nope")

    @router.get("/no-session")
    async def no_session() -> dict[str, str]:
        return {"status": "read"}

    app = FastAPI()
    app.state.session_factory = RecordingSessionFactory(events)
    app.include_router(router)

    async def spying_app(scope: Scope, receive: Receive, send: Send) -> None:
        async def spy(message: Any) -> None:
            events.append(str(message["type"]))
            await send(message)

        await app(scope, receive, spy)

    transport = ASGITransport(app=spying_app)
    async with AsyncClient(transport=transport, base_url="http://test") as http_client:
        yield http_client


async def test_the_session_is_committed_before_the_response_is_sent(
    client: AsyncClient, events: list[str]
) -> None:
    response = await client.post("/write")

    assert response.status_code == 200
    # The exact order is the assertion: a client reading straight after this sees its own write.
    assert events == ["commit", "http.response.start", "http.response.body"]


async def test_a_failing_request_rolls_back_and_never_commits(
    client: AsyncClient, events: list[str]
) -> None:
    response = await client.post("/fail")

    assert response.status_code == 400
    # The rollback also lands before the error response — no window in which a half-written
    # transaction is visible to a client that has already been told the request failed.
    assert events == ["rollback", "http.response.start", "http.response.body"]


async def test_a_route_that_never_asked_for_a_session_is_left_alone(
    client: AsyncClient, events: list[str]
) -> None:
    response = await client.get("/no-session")

    assert response.status_code == 200
    # No session was opened, so there is nothing to commit and no wasted round trip.
    assert events == ["http.response.start", "http.response.body"]


async def test_a_session_route_on_a_plain_router_is_refused_at_startup() -> None:
    """The footgun this design creates, turned into a boot failure.

    ``get_session`` no longer commits, so a router wired without ``route_class`` would return a
    ``2xx`` while discarding every write. Silent data loss is worse than the race it replaced, so
    the composition root rejects it.
    """
    plain = APIRouter()

    @plain.post("/forgot-the-route-class")
    async def forgot(session: SessionDep) -> dict[str, str]:
        return {"status": "silently lost"}

    with pytest.raises(RuntimeError, match=r"POST /forgot-the-route-class"):
        assert_session_routes_are_transactional(plain)


async def test_a_router_with_no_session_routes_passes() -> None:
    plain = APIRouter()

    @plain.get("/harmless")
    async def harmless() -> dict[str, str]:
        return {"status": "read-only"}

    # No session, nothing to commit — a plain router is fine and must not be flagged.
    assert_session_routes_are_transactional(plain)


def test_the_real_application_passes_the_guard() -> None:
    # create_app runs the guard over its own routers, so constructing it is the assertion. Named
    # explicitly so a failure reads as "a real router lost its route_class".
    assert create_app() is not None
