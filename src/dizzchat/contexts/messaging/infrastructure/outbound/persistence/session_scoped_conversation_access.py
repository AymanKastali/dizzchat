"""A ``ConversationAccess`` adapter that checks access in its own read transaction.

The WebSocket connect handler cannot borrow the request-scoped session, so the one-shot access
check opens (and closes) its own session, delegating the rule to ``EnsureConversationAccess``.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from dizzchat.contexts.messaging.application.services import EnsureConversationAccess
from dizzchat.contexts.messaging.domain.conversation import ConversationId, OwnerId
from dizzchat.contexts.messaging.infrastructure.outbound.persistence.repositories import (
    SqlAlchemyConversationRepository,
)


class SessionScopedConversationAccess:
    """Implements the ``ConversationAccess`` port using a short-lived read session."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def ensure(self, *, conversation_id: ConversationId, owner_id: OwnerId) -> None:
        async with self._session_factory() as session:
            await EnsureConversationAccess(SqlAlchemyConversationRepository(session)).execute(
                conversation_id=conversation_id, owner_id=owner_id
            )
