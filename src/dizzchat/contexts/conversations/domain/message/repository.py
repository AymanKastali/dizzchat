"""Repository port for the Message aggregate root.

Declared in the domain (the aggregate owns the contract for its own persistence); the concrete
adapter lives in ``infrastructure/outbound/persistence``. Write methods take attributes rather
than the aggregate, so the persistence layer assigns the identity and cannot mutate domain state.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from dizzchat.contexts.conversations.domain.conversation import ConversationId
from dizzchat.contexts.conversations.domain.message.message import Message
from dizzchat.contexts.conversations.domain.message.value_objects import (
    MessageContent,
    MessageId,
    SenderId,
)


class MessageRepository(Protocol):
    """Persistence for the ``Message`` aggregate root."""

    async def create(
        self,
        *,
        conversation_id: ConversationId,
        sender_id: SenderId,
        content: MessageContent,
        created_at: datetime,
    ) -> Message:
        """Persist a new message, returning it with its store-assigned ``id`` (the ordering seq)."""
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
