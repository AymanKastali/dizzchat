"""List-conversations use case."""

from __future__ import annotations

from dizzchat.contexts.conversations.domain.conversation import (
    Conversation,
    ConversationRepository,
    OwnerId,
)


class ListConversations:
    """Return an owner's active conversations, newest first."""

    def __init__(self, conversations: ConversationRepository) -> None:
        self._conversations = conversations

    async def execute(self, *, owner_id: OwnerId) -> list[Conversation]:
        return await self._conversations.list_for_owner(owner_id)
