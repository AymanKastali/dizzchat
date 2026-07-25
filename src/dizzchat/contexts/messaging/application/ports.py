"""Technical ports the real-time messaging use cases depend on.

These abstract infrastructure capabilities the domain must not know about — a mock AI responder,
local/Redis broadcast, and the transactional message writer. Concrete adapters live in
``infrastructure/outbound``. Keeping them behind ports lets the exchange flow be unit-tested with
fakes and lets Slice 5 slot Redis in behind ``MessageBroadcaster`` unchanged.
"""

from __future__ import annotations

from typing import Protocol

from dizzchat.contexts.messaging.domain.conversation import ConversationId
from dizzchat.contexts.messaging.domain.message import Message, MessageContent, SenderId


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
    ) -> Message:
        """Persist and return a user message (raising if the sender may not post)."""
        ...

    async def from_assistant(
        self,
        *,
        conversation_id: ConversationId,
        content: MessageContent,
    ) -> Message:
        """Persist and return an assistant message."""
        ...
