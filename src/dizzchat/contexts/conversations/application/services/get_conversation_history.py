"""Get-conversation-history use case (cursor-paginated)."""

from __future__ import annotations

from dizzchat.contexts.conversations.application.dto import MessagePage
from dizzchat.contexts.conversations.domain.conversation import (
    ConversationId,
    ConversationNotFound,
    ConversationRepository,
    OwnerId,
)
from dizzchat.contexts.conversations.domain.message import MessageId, MessageRepository


class GetConversationHistory:
    """Return a page of a conversation's messages, newest first, for its owner."""

    def __init__(self, conversations: ConversationRepository, messages: MessageRepository) -> None:
        self._conversations = conversations
        self._messages = messages

    async def execute(
        self,
        *,
        conversation_id: ConversationId,
        owner_id: OwnerId,
        before: MessageId | None,
        limit: int,
    ) -> MessagePage:
        conversation = await self._conversations.get(conversation_id)
        if conversation is None:
            raise ConversationNotFound(conversation_id)
        conversation.ensure_owned_by(owner_id)
        # Over-fetch by one to learn whether an older page exists without a second query.
        fetched = await self._messages.list_history(conversation_id, before=before, limit=limit + 1)
        has_more = len(fetched) > limit
        items = tuple(fetched[:limit])
        next_cursor = items[-1].id if has_more and items else None
        return MessagePage(items=items, next_cursor=next_cursor, has_more=has_more)
