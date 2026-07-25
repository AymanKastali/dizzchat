"""Message-exchange use case — the real-time send flow for one inbound message."""

from __future__ import annotations

from dizzchat.contexts.messaging.application.ports import (
    AssistantResponder,
    MessageBroadcaster,
    MessageWriter,
)
from dizzchat.contexts.messaging.domain.conversation import ConversationId
from dizzchat.contexts.messaging.domain.message import (
    ClientMessageId,
    Message,
    MessageContent,
    SenderId,
)


class MessageExchange:
    """Persist an inbound user message, broadcast it, then generate and broadcast the reply.

    Each message is persisted (and committed) before it is broadcast, so a subscriber never sees
    a message that a later rollback would erase. A failure of the responder or the assistant write
    propagates to the caller *after* the user message is already durable and broadcast — the socket
    adapter turns it into an ``error`` frame without dropping the connection.
    """

    def __init__(
        self,
        writer: MessageWriter,
        responder: AssistantResponder,
        broadcaster: MessageBroadcaster,
    ) -> None:
        self._writer = writer
        self._responder = responder
        self._broadcaster = broadcaster

    async def exchange(
        self,
        *,
        conversation_id: ConversationId,
        sender_id: SenderId,
        content: MessageContent,
        client_message_id: ClientMessageId | None = None,
    ) -> Message:
        """Run the send flow, returning the persisted user message (for the ack).

        A duplicate ``client_message_id`` returns the already-persisted user message without
        re-broadcasting or generating a second assistant reply (idempotent send).
        """
        user_message, created = await self._writer.from_user(
            conversation_id=conversation_id,
            sender_id=sender_id,
            content=content,
            client_message_id=client_message_id,
        )
        if not created:
            return user_message

        await self._broadcaster.broadcast(conversation_id, user_message)

        reply = await self._responder.reply_to(content)
        assistant_message = await self._writer.from_assistant(
            conversation_id=conversation_id, content=reply
        )
        await self._broadcaster.broadcast(conversation_id, assistant_message)
        return user_message
