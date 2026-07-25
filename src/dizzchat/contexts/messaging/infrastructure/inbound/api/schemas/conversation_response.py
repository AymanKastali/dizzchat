"""Conversation response schema."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from dizzchat.contexts.messaging.domain.conversation import Conversation


class ConversationResponse(BaseModel):
    """A conversation as returned to clients."""

    id: UUID
    owner_id: UUID
    title: str
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_domain(cls, conversation: Conversation) -> ConversationResponse:
        return cls(
            id=conversation.id.value,
            owner_id=conversation.owner_id.value,
            title=conversation.title.value,
            created_at=conversation.created_at,
            updated_at=conversation.updated_at,
        )
