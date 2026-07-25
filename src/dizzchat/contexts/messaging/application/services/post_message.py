"""Post-message use case — append a user or assistant message to a conversation."""

from __future__ import annotations

from dizzchat.contexts.messaging.domain.conversation import (
    ConversationId,
    ConversationNotFound,
    ConversationRepository,
    OwnerId,
)
from dizzchat.contexts.messaging.domain.message import (
    Message,
    MessageContent,
    MessageRepository,
    MessageRole,
    SenderId,
)
from dizzchat.shared.application import Clock


class PostMessage:
    """Append a message to a conversation, persisting it with the store-assigned ordering id.

    Does not commit — the caller (the request/socket unit of work) owns the transaction.
    """

    def __init__(
        self,
        conversations: ConversationRepository,
        messages: MessageRepository,
        clock: Clock,
    ) -> None:
        self._conversations = conversations
        self._messages = messages
        self._clock = clock

    async def from_user(
        self,
        *,
        conversation_id: ConversationId,
        sender_id: SenderId,
        content: MessageContent,
    ) -> Message:
        """Persist a user message, after checking the sender owns the conversation."""
        conversation = await self._conversations.get(conversation_id)
        if conversation is None:
            raise ConversationNotFound(conversation_id)
        conversation.ensure_owned_by(OwnerId(sender_id.value))
        return await self._messages.create(
            conversation_id=conversation_id,
            sender_id=sender_id,
            role=MessageRole.USER,
            content=content,
            created_at=self._clock.now(),
        )

    async def from_assistant(
        self,
        *,
        conversation_id: ConversationId,
        content: MessageContent,
    ) -> Message:
        """Persist an assistant message (no sender). The conversation is assumed to exist."""
        return await self._messages.create(
            conversation_id=conversation_id,
            sender_id=None,
            role=MessageRole.ASSISTANT,
            content=content,
            created_at=self._clock.now(),
        )
