"""Message response schema."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from dizzchat.contexts.conversations.domain.message import Message


class MessageResponse(BaseModel):
    """A message as returned to clients; ``id`` is the ordering sequence number."""

    id: int
    conversation_id: UUID
    sender_id: UUID
    content: str
    created_at: datetime

    @classmethod
    def from_domain(cls, message: Message) -> MessageResponse:
        return cls(
            id=message.id.value,
            conversation_id=message.conversation_id.value,
            sender_id=message.sender_id.value,
            content=message.content.value,
            created_at=message.created_at,
        )
