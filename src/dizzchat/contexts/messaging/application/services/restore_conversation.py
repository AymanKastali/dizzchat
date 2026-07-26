"""Restore-conversation use case (undo a soft-delete)."""

from __future__ import annotations

from dizzchat.contexts.messaging.domain.conversation import (
    Conversation,
    ConversationId,
    ConversationNotFound,
    ConversationRepository,
    OwnerId,
)
from dizzchat.shared.application import Clock


class RestoreConversation:
    """Undo the soft-delete of a conversation the caller owns.

    The only use case that reads through ``get_including_deleted``: every other one must not see a
    deleted conversation, but this one exists precisely to act on it. Restoring an already-active
    conversation succeeds without changing anything, so a retry is harmless.
    """

    def __init__(self, conversations: ConversationRepository, clock: Clock) -> None:
        self._conversations = conversations
        self._clock = clock

    async def execute(self, *, conversation_id: ConversationId, owner_id: OwnerId) -> Conversation:
        conversation = await self._conversations.get_including_deleted(conversation_id)
        if conversation is None:
            raise ConversationNotFound(conversation_id)
        conversation.ensure_owned_by(owner_id)
        conversation.restore(self._clock.now())
        await self._conversations.update(
            conversation_id=conversation.id,
            title=conversation.title,
            updated_at=conversation.updated_at,
            deleted_at=conversation.deleted_at,
        )
        return conversation
