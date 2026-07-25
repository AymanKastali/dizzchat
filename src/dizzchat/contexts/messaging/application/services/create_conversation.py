"""Create-conversation use case."""

from __future__ import annotations

from uuid import uuid4

from dizzchat.contexts.messaging.domain.conversation import (
    Conversation,
    ConversationId,
    ConversationRepository,
    ConversationTitle,
    OwnerId,
)
from dizzchat.shared.application import Clock


class CreateConversation:
    """Start a new conversation for an owner."""

    def __init__(self, conversations: ConversationRepository, clock: Clock) -> None:
        self._conversations = conversations
        self._clock = clock

    async def execute(self, *, owner_id: OwnerId, title: str) -> Conversation:
        conversation = Conversation.start(
            conversation_id=ConversationId(uuid4()),
            owner_id=owner_id,
            title=ConversationTitle(title),
            created_at=self._clock.now(),
        )
        await self._conversations.create(
            conversation_id=conversation.id,
            owner_id=conversation.owner_id,
            title=conversation.title,
            created_at=conversation.created_at,
            updated_at=conversation.updated_at,
        )
        return conversation
