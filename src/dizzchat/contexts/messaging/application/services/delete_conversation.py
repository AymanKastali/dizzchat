"""Delete-conversation use case (soft-delete)."""

from __future__ import annotations

from dizzchat.contexts.messaging.domain.conversation import (
    ConversationId,
    ConversationNotFound,
    ConversationRepository,
    OwnerId,
)
from dizzchat.shared.application import Clock


class DeleteConversation:
    """Soft-delete a conversation the caller owns."""

    def __init__(self, conversations: ConversationRepository, clock: Clock) -> None:
        self._conversations = conversations
        self._clock = clock

    async def execute(self, *, conversation_id: ConversationId, owner_id: OwnerId) -> None:
        conversation = await self._conversations.get(conversation_id)
        if conversation is None:
            raise ConversationNotFound(conversation_id)
        conversation.ensure_owned_by(owner_id)
        conversation.delete(self._clock.now())
        await self._conversations.update(
            conversation_id=conversation.id,
            title=conversation.title,
            updated_at=conversation.updated_at,
            deleted_at=conversation.deleted_at,
        )
