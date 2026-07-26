"""A route class that commits the request-scoped session before the response is sent.

Where the transaction boundary sits matters more than it looks. FastAPI runs the teardown of a
``yield`` dependency on the *request* exit stack, which unwinds only after
``await response(scope, receive, send)`` — so committing in ``get_session``'s post-``yield`` block
would acknowledge a write to the client *before* it was durable. A client that immediately reads
what it just wrote (sign up, then log in) can then be served a snapshot without its own row.

A custom route handler runs inside the same call that produces the response object and returns
before it is sent, which is exactly the window that teardown misses. Committing here makes the HTTP
request the transaction boundary in fact and not just in intent, and it applies to every route on
the router — including ones added later, which is why this is a route class rather than a commit
repeated in each controller.
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine, Iterable, Iterator
from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute
from sqlalchemy.ext.asyncio import AsyncSession

from dizzchat.shared.infrastructure.inbound.api.dependencies import get_session


class TransactionalRoute(APIRoute):
    """Commits the session opened by ``get_session``, if the route used one, before responding."""

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()

        async def commit_before_responding(request: Request) -> Response:
            response = await handler(request)
            # Absent on routes that never asked for a session, and on those the commit would be a
            # pointless round trip. A raising handler never reaches here at all: the exception
            # unwinds past this frame into ``get_session``, which rolls back.
            session: AsyncSession | None = getattr(request.state, "db_session", None)
            if session is not None:
                await session.commit()
            return response

        return commit_before_responding


def assert_session_routes_are_transactional(*routers: APIRouter) -> None:
    """Fail startup if a route takes a session but is not registered to commit it.

    Since ``get_session`` no longer commits, a router registered without
    ``route_class=TransactionalRoute`` would open a transaction, do the work, and then discard it —
    every write silently lost behind a ``2xx``. That is a worse failure than the race this design
    replaces, so the composition root refuses to boot rather than leaving it to a passing test suite
    and a puzzled operator.

    Takes the routers themselves rather than ``app.routes``: since FastAPI 0.140 ``include_router``
    stores an opaque wrapper on the app, whereas ``APIRouter.routes`` is the public list of the
    routes actually registered.
    """
    offenders = [
        f"{'/'.join(sorted(route.methods or []))} {route.path}"
        for route in _api_routes(routers)
        if not isinstance(route, TransactionalRoute) and _depends_on_session(route.dependant)
    ]
    if offenders:
        raise RuntimeError(
            "these routes depend on get_session but their router is missing "
            f"route_class=TransactionalRoute, so their writes would never commit: {offenders}"
        )


def _api_routes(routers: Iterable[APIRouter]) -> Iterator[APIRoute]:
    """Every ``APIRoute`` reachable from these routers, following any nesting."""
    for router in routers:
        for route in router.routes:
            if isinstance(route, APIRoute):
                yield route
            nested = getattr(route, "original_router", None)
            if isinstance(nested, APIRouter):
                yield from _api_routes([nested])


def _depends_on_session(dependant: Dependant) -> bool:
    """Whether ``get_session`` appears anywhere in this route's dependency tree."""
    if dependant.call is get_session:
        return True
    return any(_depends_on_session(sub) for sub in dependant.dependencies)
