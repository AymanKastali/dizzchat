"""Tests for the MessageExchange use case (persist -> broadcast -> reply -> broadcast)."""

from uuid import uuid4

import pytest

from dizzchat.contexts.messaging.application.services import MessageExchange
from dizzchat.contexts.messaging.domain.conversation import ConversationId
from dizzchat.contexts.messaging.domain.message import (
    ClientMessageId,
    MessageContent,
    MessageRole,
    SenderId,
)
from tests.contexts.messaging.fakes import (
    CannedAssistantResponder,
    FailingAssistantResponder,
    FakeMessageWriter,
    RecordingBroadcaster,
)


async def test_persists_and_broadcasts_the_user_message_then_the_assistant_reply() -> None:
    writer, broadcaster = FakeMessageWriter(), RecordingBroadcaster()
    exchange = MessageExchange(writer, CannedAssistantResponder("echo"), broadcaster)
    conversation_id, sender_id = ConversationId(uuid4()), SenderId(uuid4())

    user_message = await exchange.exchange(
        conversation_id=conversation_id, sender_id=sender_id, content=MessageContent("hi")
    )

    # Both messages persisted, user first.
    assert [m.role for m in writer.written] == [MessageRole.USER, MessageRole.ASSISTANT]
    assert writer.written[0].content.value == "hi"
    assert writer.written[1].content.value == "echo"
    # Both broadcast to the same conversation, user first; the user message is returned for the ack.
    assert [m.role for _, m in broadcaster.broadcasts] == [MessageRole.USER, MessageRole.ASSISTANT]
    assert all(cid == conversation_id for cid, _ in broadcaster.broadcasts)
    assert broadcaster.broadcasts[0][1] is user_message


async def test_a_duplicate_send_returns_existing_without_re_broadcast_or_assistant_reply() -> None:
    writer, broadcaster = FakeMessageWriter(), RecordingBroadcaster()
    exchange = MessageExchange(writer, CannedAssistantResponder("echo"), broadcaster)
    conversation_id, sender_id = ConversationId(uuid4()), SenderId(uuid4())
    client_message_id = ClientMessageId(uuid4())

    first = await exchange.exchange(
        conversation_id=conversation_id,
        sender_id=sender_id,
        content=MessageContent("hi"),
        client_message_id=client_message_id,
    )
    broadcaster.broadcasts.clear()
    second = await exchange.exchange(
        conversation_id=conversation_id,
        sender_id=sender_id,
        content=MessageContent("hi"),
        client_message_id=client_message_id,
    )

    assert second.id == first.id  # same server message returned for the ack
    # No second user row, no assistant turn (from_assistant would append), no re-broadcast.
    assert [m.role for m in writer.written] == [MessageRole.USER, MessageRole.ASSISTANT]
    assert broadcaster.broadcasts == []


async def test_a_failing_assistant_leaves_the_user_message_persisted_and_broadcast() -> None:
    writer, broadcaster = FakeMessageWriter(), RecordingBroadcaster()
    exchange = MessageExchange(writer, FailingAssistantResponder(), broadcaster)

    with pytest.raises(RuntimeError):
        await exchange.exchange(
            conversation_id=ConversationId(uuid4()),
            sender_id=SenderId(uuid4()),
            content=MessageContent("hi"),
        )

    # The user message survived; no assistant message was persisted or broadcast.
    assert [m.role for m in writer.written] == [MessageRole.USER]
    assert [m.role for _, m in broadcaster.broadcasts] == [MessageRole.USER]
