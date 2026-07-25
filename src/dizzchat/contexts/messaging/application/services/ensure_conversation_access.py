"""Ensure-conversation-access use case — the authorization check for joining a conversation."""

from __future__ import annotations

from dizzchat.contexts.messaging.domain.conversation import (
    ConversationId,
    ConversationNotFound,
    ConversationRepository,
    OwnerId,
)


class EnsureConversationAccess:
    """Assert that an owner may access a conversation, raising a domain error otherwise.

    Used at WebSocket connect so an unauthorized socket is rejected before it is registered for
    broadcasts (and never sees another user's messages).
    """

    def __init__(self, conversations: ConversationRepository) -> None:
        self._conversations = conversations

    async def execute(self, *, conversation_id: ConversationId, owner_id: OwnerId) -> None:
        conversation = await self._conversations.get(conversation_id)
        if conversation is None:
            raise ConversationNotFound(conversation_id)
        conversation.ensure_owned_by(owner_id)
