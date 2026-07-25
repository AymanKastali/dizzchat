"""Unit tests for the Message aggregate and its value objects."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from dizzchat.contexts.messaging.domain.conversation import ConversationId
from dizzchat.contexts.messaging.domain.message import (
    ClientMessageId,
    InvalidMessageContent,
    Message,
    MessageContent,
    MessageId,
    MessageRole,
    SenderId,
)

_CREATED_AT = datetime(2024, 1, 1, tzinfo=UTC)


def test_content_rejects_a_blank_value() -> None:
    with pytest.raises(InvalidMessageContent):
        MessageContent("   ")


def test_content_accepts_a_non_empty_value() -> None:
    assert MessageContent("hello").value == "hello"


def test_message_role_values_are_the_lowercase_names() -> None:
    assert MessageRole.USER.value == "user"
    assert MessageRole.ASSISTANT.value == "assistant"


def _message(message_id: int) -> Message:
    return Message(
        id=MessageId(message_id),
        conversation_id=ConversationId(uuid4()),
        sender_id=SenderId(uuid4()),
        role=MessageRole.USER,
        content=MessageContent("hi"),
        created_at=_CREATED_AT,
    )


def test_messages_are_equal_by_identity_not_fields() -> None:
    assert _message(1) == _message(1)
    assert _message(1) != _message(2)


def test_client_message_id_is_equal_by_value() -> None:
    value = uuid4()
    assert ClientMessageId(value) == ClientMessageId(value)
    assert str(ClientMessageId(value)) == str(value)


def test_message_carries_an_optional_client_message_id() -> None:
    cid = ClientMessageId(uuid4())
    message = Message(
        id=MessageId(1),
        conversation_id=ConversationId(uuid4()),
        sender_id=SenderId(uuid4()),
        role=MessageRole.USER,
        content=MessageContent("hi"),
        created_at=_CREATED_AT,
        client_message_id=cid,
    )

    assert message.client_message_id == cid


def test_a_message_defaults_to_no_client_message_id() -> None:
    assert _message(1).client_message_id is None


def test_an_assistant_message_has_no_sender() -> None:
    message = Message(
        id=MessageId(1),
        conversation_id=ConversationId(uuid4()),
        sender_id=None,
        role=MessageRole.ASSISTANT,
        content=MessageContent("hello from the assistant"),
        created_at=_CREATED_AT,
    )

    assert message.sender_id is None
    assert message.role is MessageRole.ASSISTANT
