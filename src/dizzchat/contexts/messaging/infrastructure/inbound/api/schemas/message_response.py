"""Message response schema."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from dizzchat.contexts.messaging.domain.message import Message


class MessageResponse(BaseModel):
    """A message as returned to clients; ``id`` is the ordering sequence number.

    ``sender_id`` is ``null`` for an assistant message.
    """

    id: int
    conversation_id: UUID
    sender_id: UUID | None
    role: str
    content: str
    created_at: datetime

    @classmethod
    def from_domain(cls, message: Message) -> MessageResponse:
        return cls(
            id=message.id.value,
            conversation_id=message.conversation_id.value,
            sender_id=message.sender_id.value if message.sender_id is not None else None,
            role=message.role.value,
            content=message.content.value,
            created_at=message.created_at,
        )
