"""Technical ports the real-time messaging use cases depend on.

These abstract infrastructure capabilities the domain must not know about — a mock AI responder,
local/Redis broadcast, and the transactional message writer. Concrete adapters live in
``infrastructure/outbound``. Keeping them behind ports lets the exchange flow be unit-tested with
fakes and lets Slice 5 slot Redis in behind ``MessageBroadcaster`` unchanged.
"""

from __future__ import annotations

from typing import Protocol

from dizzchat.contexts.messaging.domain.conversation import ConversationId, OwnerId
from dizzchat.contexts.messaging.domain.message import (
    ClientMessageId,
    Message,
    MessageContent,
    MessageId,
    SenderId,
)


class ConversationAccess(Protocol):
    """Asserts an owner may access a conversation, within its own read transaction."""

    async def ensure(self, *, conversation_id: ConversationId, owner_id: OwnerId) -> None:
        """Return normally if allowed, or raise a conversation domain error."""
        ...


class AssistantResponder(Protocol):
    """Generates the assistant's reply to a message. A mock in this project."""

    async def reply_to(self, prompt: MessageContent) -> MessageContent:
        """Return the assistant's reply to ``prompt``."""
        ...


class MessageBroadcaster(Protocol):
    """Delivers a persisted message to the live subscribers of its conversation.

    Takes a domain ``Message`` (not a wire frame); the transport adapter owns serialization.
    """

    async def broadcast(self, conversation_id: ConversationId, message: Message) -> None:
        """Deliver ``message`` to every socket currently subscribed to ``conversation_id``."""
        ...


class MessageWriter(Protocol):
    """Persists a message within its own unit of work (one committed transaction per call).

    A long-lived socket cannot reuse the request-scoped session, so each write opens and commits
    its own session; this port hides that from the exchange flow and is the seam tests fake.
    """

    async def from_user(
        self,
        *,
        conversation_id: ConversationId,
        sender_id: SenderId,
        content: MessageContent,
        client_message_id: ClientMessageId | None = None,
    ) -> tuple[Message, bool]:
        """Persist a user message, or return the existing one for a duplicate ``client_message_id``.

        Returns ``(message, created)``; ``created`` is ``False`` on an idempotent duplicate send.
        Raises if the sender may not post.
        """
        ...

    async def from_assistant(
        self,
        *,
        conversation_id: ConversationId,
        content: MessageContent,
    ) -> Message:
        """Persist and return an assistant message."""
        ...


class MessageReplayer(Protocol):
    """Reads the messages a reconnecting client missed, within its own read transaction."""

    async def replay_since(
        self, *, conversation_id: ConversationId, after: MessageId | None
    ) -> list[Message]:
        """Return every message with ordering id above ``after``, oldest-first."""
        ...
