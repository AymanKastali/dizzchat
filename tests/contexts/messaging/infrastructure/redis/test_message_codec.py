"""Round-trip tests for the Redis pub/sub message codec.

``Message`` equality is by id only, so these assert every reconstructed field explicitly rather
than relying on ``==``.
"""

from datetime import UTC, datetime
from uuid import uuid4

from dizzchat.contexts.messaging.domain.conversation import ConversationId
from dizzchat.contexts.messaging.domain.message import (
    Message,
    MessageContent,
    MessageId,
    MessageRole,
    SenderId,
)
from dizzchat.contexts.messaging.infrastructure.outbound.redis.message_codec import decode, encode

_NOW = datetime(2024, 1, 1, 12, 0, tzinfo=UTC)


def test_round_trip_preserves_a_user_message() -> None:
    conversation_id = ConversationId(uuid4())
    sender_id = SenderId(uuid4())
    message = Message(
        id=MessageId(7),
        conversation_id=conversation_id,
        sender_id=sender_id,
        role=MessageRole.USER,
        content=MessageContent("hi"),
        created_at=_NOW,
    )

    decoded = decode(encode(message))

    assert decoded.id == MessageId(7)
    assert decoded.conversation_id == conversation_id
    assert decoded.sender_id == sender_id
    assert decoded.role == MessageRole.USER
    assert decoded.content == MessageContent("hi")
    assert decoded.created_at == _NOW


def test_round_trip_preserves_an_assistant_message_with_no_sender() -> None:
    message = Message(
        id=MessageId(8),
        conversation_id=ConversationId(uuid4()),
        sender_id=None,
        role=MessageRole.ASSISTANT,
        content=MessageContent("You said: hi"),
        created_at=_NOW,
    )

    decoded = decode(encode(message))

    assert decoded.sender_id is None
    assert decoded.role == MessageRole.ASSISTANT
    assert decoded.content == MessageContent("You said: hi")
