"""FastAPI application factory and the composition root.

Concrete infrastructure adapters (Redis pub/sub, the WebSocket connection manager, the DB engine)
are wired here, and their lifecycle is managed in ``lifespan``.
"""

import asyncio
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from dizzchat.config import Settings, get_settings
from dizzchat.contexts.identity.infrastructure.inbound.api.errors import (
    register_identity_error_handlers,
)
from dizzchat.contexts.identity.infrastructure.inbound.api.router import router as identity_router
from dizzchat.contexts.messaging.infrastructure.inbound.api.errors import (
    register_messaging_error_handlers,
)
from dizzchat.contexts.messaging.infrastructure.inbound.api.realtime import (
    ConnectionManager,
    ConversationRegistry,
    ws_router,
)
from dizzchat.contexts.messaging.infrastructure.inbound.api.router import (
    router as conversations_router,
)
from dizzchat.contexts.messaging.infrastructure.outbound.redis import (
    RedisConversationSubscriber,
    RedisMessageBroadcaster,
    RedisRateLimiter,
)
from dizzchat.logging import configure_logging
from dizzchat.shared.infrastructure.inbound.api.health import router as health_router
from dizzchat.shared.infrastructure.inbound.api.transactional_route import (
    assert_session_routes_are_transactional,
)
from dizzchat.shared.infrastructure.outbound import SystemClock
from dizzchat.shared.infrastructure.outbound.database import create_engine, create_session_factory
from dizzchat.shared.infrastructure.outbound.migrations import run_migrations
from dizzchat.shared.infrastructure.outbound.redis_client import create_redis_client

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """Apply migrations, open the DB + Redis infrastructure, and drain it on shutdown."""
    # Alembic's runner is synchronous and opens its own event loop (env.py uses asyncio.run),
    # so run it in a worker thread rather than nesting it in this loop. Concurrent replicas
    # are serialized by an advisory lock (see migrations/env.py); a failure aborts startup.
    logger.info("applying database migrations")
    await asyncio.to_thread(run_migrations)
    logger.info("database migrations applied")

    settings = get_settings()
    engine = create_engine(settings.database_url)
    app.state.engine = engine
    app.state.session_factory = create_session_factory(engine)

    # Per-replica fan-out: the connection manager is the local delivery half; the subscriber pulls
    # this replica's subscribed conversations off Redis into it; the broadcaster publishes; the
    # registry ties a socket's local registration to Redis (un)subscription.
    redis_client = create_redis_client(settings.redis_url)
    app.state.redis = redis_client
    connection_manager = ConnectionManager()
    app.state.connection_manager = connection_manager
    subscriber = RedisConversationSubscriber(redis_client, connection_manager)
    await subscriber.start()
    app.state.message_broadcaster = RedisMessageBroadcaster(redis_client)
    app.state.conversation_registry = ConversationRegistry(connection_manager, subscriber)
    # Second use of Redis, unrelated to fan-out: the per-user frame counter, shared so a client
    # cannot reset its quota by reconnecting to the other replica.
    app.state.rate_limiter = RedisRateLimiter(
        redis_client,
        SystemClock(),
        limit=settings.ws_rate_limit_messages,
        window_seconds=settings.ws_rate_limit_window_seconds,
    )
    try:
        yield
    finally:
        # Graceful shutdown (uvicorn routes SIGTERM here and stops accepting new connections):
        # drain live sockets, stop the subscriber, close Redis, then dispose the DB pool.
        try:
            await asyncio.wait_for(
                connection_manager.close_all(), timeout=settings.shutdown_drain_timeout_seconds
            )
        except TimeoutError:
            logger.warning("socket drain timed out; shutting down anyway")
        await subscriber.stop()
        await redis_client.aclose()
        await engine.dispose()


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build a configured FastAPI application."""
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    app = FastAPI(title="dizzchat", version="0.1.0", lifespan=lifespan)
    # A wildcard origin with credentials is an unsafe combination (it echoes any Origin back
    # with Access-Control-Allow-Credentials: true), so only allow credentials for explicit origins.
    allow_credentials = "*" not in settings.cors_allow_origins
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allow_origins,
        allow_credentials=allow_credentials,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    register_identity_error_handlers(app)
    register_messaging_error_handlers(app)
    app.include_router(health_router)
    app.include_router(identity_router)
    app.include_router(conversations_router)
    app.include_router(ws_router)
    # Composition-root guard: the commit lives in TransactionalRoute, so a router wired without it
    # would drop its writes silently. Refuse to boot instead.
    assert_session_routes_are_transactional(
        health_router, identity_router, conversations_router, ws_router
    )
    return app


app = create_app()
