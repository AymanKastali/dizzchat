"""Post-message use case — append a user or assistant message to a conversation."""

from __future__ import annotations

from dizzchat.contexts.messaging.domain.conversation import (
    ConversationId,
    ConversationNotFound,
    ConversationRepository,
    ParticipantId,
)
from dizzchat.contexts.messaging.domain.message import (
    ClientMessageId,
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
        client_message_id: ClientMessageId | None = None,
    ) -> tuple[Message, bool]:
        """Persist a user message, or return the existing one for a duplicate client id.

        Returns ``(message, created)``; ``created`` is ``False`` when ``client_message_id`` already
        has a persisted message (idempotent send). The DB unique constraint is the backstop for a
        concurrent double-send that races past this pre-flight check.
        """
        conversation = await self._conversations.get(conversation_id)
        if conversation is None:
            raise ConversationNotFound(conversation_id)
        # Re-checked on every message, so revoking a membership stops that user posting immediately
        # (even on an already-open socket, whose access was checked only at connect).
        conversation.ensure_participant(ParticipantId(sender_id.value))

        if client_message_id is not None:
            existing = await self._messages.find_by_client_message_id(
                conversation_id, client_message_id
            )
            if existing is not None:
                return existing, False

        message = await self._messages.create(
            conversation_id=conversation_id,
            sender_id=sender_id,
            role=MessageRole.USER,
            content=content,
            created_at=self._clock.now(),
            client_message_id=client_message_id,
        )
        return message, True

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
