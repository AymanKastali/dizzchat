"""Unit tests for the Message aggregate and its value objects."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from dizzchat.contexts.conversations.domain.conversation import ConversationId
from dizzchat.contexts.conversations.domain.message import (
    InvalidMessageContent,
    Message,
    MessageContent,
    MessageId,
    SenderId,
)

_CREATED_AT = datetime(2024, 1, 1, tzinfo=UTC)


def test_content_rejects_a_blank_value() -> None:
    with pytest.raises(InvalidMessageContent):
        MessageContent("   ")


def test_content_accepts_a_non_empty_value() -> None:
    assert MessageContent("hello").value == "hello"


def _message(message_id: int) -> Message:
    return Message(
        id=MessageId(message_id),
        conversation_id=ConversationId(uuid4()),
        sender_id=SenderId(uuid4()),
        content=MessageContent("hi"),
        created_at=_CREATED_AT,
    )


def test_messages_are_equal_by_identity_not_fields() -> None:
    assert _message(1) == _message(1)
    assert _message(1) != _message(2)
