"""A ``MessageWriter`` adapter that commits one transaction per message.

A WebSocket connection is long-lived, so it cannot borrow the request-scoped session (which
commits once per HTTP request). Each write therefore opens its own session, delegates the domain
work to ``PostMessage``, and commits — one durable transaction per message.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from dizzchat.contexts.messaging.application.services import PostMessage
from dizzchat.contexts.messaging.domain.conversation import ConversationId
from dizzchat.contexts.messaging.domain.message import (
    ClientMessageId,
    Message,
    MessageContent,
    SenderId,
)
from dizzchat.contexts.messaging.infrastructure.outbound.persistence.repositories import (
    SqlAlchemyConversationRepository,
    SqlAlchemyMessageRepository,
)
from dizzchat.shared.application import Clock


class SessionScopedMessageWriter:
    """Implements the ``MessageWriter`` port, committing each write in its own unit of work."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession], clock: Clock) -> None:
        self._session_factory = session_factory
        self._clock = clock

    async def from_user(
        self,
        *,
        conversation_id: ConversationId,
        sender_id: SenderId,
        content: MessageContent,
        client_message_id: ClientMessageId | None = None,
    ) -> tuple[Message, bool]:
        async with self._session_factory() as session:
            message, created = await self._post_message(session).from_user(
                conversation_id=conversation_id,
                sender_id=sender_id,
                content=content,
                client_message_id=client_message_id,
            )
            await session.commit()
            return message, created

    async def from_assistant(
        self,
        *,
        conversation_id: ConversationId,
        content: MessageContent,
    ) -> Message:
        async with self._session_factory() as session:
            message = await self._post_message(session).from_assistant(
                conversation_id=conversation_id, content=content
            )
            await session.commit()
            return message

    def _post_message(self, session: AsyncSession) -> PostMessage:
        return PostMessage(
            SqlAlchemyConversationRepository(session),
            SqlAlchemyMessageRepository(session),
            self._clock,
        )
