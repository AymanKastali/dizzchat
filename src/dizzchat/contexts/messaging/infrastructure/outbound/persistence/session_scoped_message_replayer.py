"""A ``MessageReplayer`` adapter that reads missed messages in its own read transaction.

The WebSocket connect handler cannot borrow the request-scoped session, so reconnect replay opens
(and closes) its own read session, delegating the query to ``ReplayMessages``.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from dizzchat.contexts.messaging.application.services import ReplayMessages
from dizzchat.contexts.messaging.domain.conversation import ConversationId
from dizzchat.contexts.messaging.domain.message import Message, MessageId
from dizzchat.contexts.messaging.infrastructure.outbound.persistence.repositories import (
    SqlAlchemyMessageRepository,
)


class SessionScopedMessageReplayer:
    """Implements the ``MessageReplayer`` port using a short-lived read session."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def replay_since(
        self, *, conversation_id: ConversationId, after: MessageId | None
    ) -> list[Message]:
        async with self._session_factory() as session:
            return await ReplayMessages(SqlAlchemyMessageRepository(session)).execute(
                conversation_id=conversation_id, after=after
            )
