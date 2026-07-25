"""FastAPI application factory and the composition root.

Later slices wire concrete infrastructure adapters (Redis pub/sub, the WebSocket connection
manager) here, and manage their lifecycle in ``lifespan``.
"""

import asyncio
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from dizzchat.config import Settings, get_settings
from dizzchat.contexts.conversations.infrastructure.inbound.api.errors import (
    register_conversations_error_handlers,
)
from dizzchat.contexts.conversations.infrastructure.inbound.api.router import (
    router as conversations_router,
)
from dizzchat.contexts.identity.infrastructure.inbound.api.errors import (
    register_identity_error_handlers,
)
from dizzchat.contexts.identity.infrastructure.inbound.api.router import router as identity_router
from dizzchat.logging import configure_logging
from dizzchat.shared.infrastructure.inbound.api.health import router as health_router
from dizzchat.shared.infrastructure.outbound.database import create_engine, create_session_factory
from dizzchat.shared.infrastructure.outbound.migrations import run_migrations

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """Apply migrations, then open/close the shared DB engine and session factory."""
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
    try:
        yield
    finally:
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
    register_conversations_error_handlers(app)
    app.include_router(health_router)
    app.include_router(identity_router)
    app.include_router(conversations_router)
    return app


app = create_app()
