"""Repository port for the Message aggregate root.

Declared in the domain (the aggregate owns the contract for its own persistence); the concrete
adapter lives in ``infrastructure/outbound/persistence``. Write methods take attributes rather
than the aggregate, so the persistence layer assigns the identity and cannot mutate domain state.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from dizzchat.contexts.messaging.domain.conversation import ConversationId
from dizzchat.contexts.messaging.domain.message.message import Message
from dizzchat.contexts.messaging.domain.message.value_objects import (
    ClientMessageId,
    MessageContent,
    MessageId,
    MessageRole,
    SenderId,
)


class MessageRepository(Protocol):
    """Persistence for the ``Message`` aggregate root."""

    async def create(
        self,
        *,
        conversation_id: ConversationId,
        sender_id: SenderId | None,
        role: MessageRole,
        content: MessageContent,
        created_at: datetime,
        client_message_id: ClientMessageId | None = None,
    ) -> Message:
        """Persist a new message, returning it with its store-assigned ``id`` (the ordering seq).

        ``sender_id`` is ``None`` for an ``ASSISTANT`` message. ``client_message_id`` is the
        client's idempotency key for a user send (``None`` for an assistant message).
        """
        ...

    async def find_by_client_message_id(
        self, conversation_id: ConversationId, client_message_id: ClientMessageId
    ) -> Message | None:
        """Return the message already stored under this conversation's client id, or ``None``."""
        ...

    async def list_history(
        self,
        conversation_id: ConversationId,
        *,
        before: MessageId | None,
        limit: int,
    ) -> list[Message]:
        """Return up to ``limit`` messages, newest-first, with id below ``before``."""
        ...

    async def list_since(
        self,
        conversation_id: ConversationId,
        *,
        after: MessageId | None,
        limit: int,
    ) -> list[Message]:
        """Return up to ``limit`` messages, oldest-first, with id above ``after`` (replay)."""
        ...
