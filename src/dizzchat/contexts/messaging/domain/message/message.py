"""Message aggregate root."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from dizzchat.contexts.messaging.domain.conversation import ConversationId
from dizzchat.contexts.messaging.domain.message.value_objects import (
    ClientMessageId,
    MessageContent,
    MessageId,
    MessageRole,
    SenderId,
)


@dataclass(eq=False, slots=True)
class Message:
    """A single message in a conversation — an immutable record identified by its ``id``.

    Its identity is the store-assigned ``bigserial`` sequence number (also the ordering key for
    history), so a ``Message`` instance always represents an already-persisted message. A
    separate aggregate from ``Conversation`` (referenced by id), so persisting a message never
    locks the conversation. An entity: equal by identity.

    ``sender_id`` is the authoring user for a ``USER`` message and ``None`` for an ``ASSISTANT``
    message (the assistant is not an Identity user). ``client_message_id`` is the client-supplied
    idempotency key for a user send, and ``None`` for an assistant message or a send that carried
    no key.
    """

    id: MessageId
    conversation_id: ConversationId
    sender_id: SenderId | None
    role: MessageRole
    content: MessageContent
    created_at: datetime
    client_message_id: ClientMessageId | None = None

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Message) and other.id == self.id

    def __hash__(self) -> int:
        return hash(self.id)
