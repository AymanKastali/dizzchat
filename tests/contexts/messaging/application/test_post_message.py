"""Tests for the PostMessage use case."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from dizzchat.contexts.messaging.application.services import CreateConversation, PostMessage
from dizzchat.contexts.messaging.domain.conversation import (
    ConversationId,
    ConversationNotFound,
    NotConversationOwner,
    OwnerId,
)
from dizzchat.contexts.messaging.domain.message import MessageContent, MessageRole, SenderId
from tests.contexts.messaging.fakes import (
    FakeConversationRepository,
    FakeMessageRepository,
    FixedClock,
)

_NOW = datetime(2024, 1, 1, tzinfo=UTC)


async def _conversation_owned_by(
    conversations: FakeConversationRepository, owner: OwnerId
) -> ConversationId:
    created = await CreateConversation(conversations, FixedClock(_NOW)).execute(
        owner_id=owner, title="c"
    )
    return created.id


async def test_from_user_persists_a_user_message_from_the_owner() -> None:
    conversations, messages = FakeConversationRepository(), FakeMessageRepository()
    owner = OwnerId(uuid4())
    conversation_id = await _conversation_owned_by(conversations, owner)

    message = await PostMessage(conversations, messages, FixedClock(_NOW)).from_user(
        conversation_id=conversation_id,
        sender_id=SenderId(owner.value),
        content=MessageContent("hello"),
    )

    assert message.role is MessageRole.USER
    assert message.sender_id == SenderId(owner.value)
    assert message.content.value == "hello"
    assert message.created_at == _NOW
    assert message.id.value == 1  # store-assigned ordering seq


async def test_from_user_rejects_a_missing_conversation() -> None:
    with pytest.raises(ConversationNotFound):
        await PostMessage(
            FakeConversationRepository(), FakeMessageRepository(), FixedClock(_NOW)
        ).from_user(
            conversation_id=ConversationId(uuid4()),
            sender_id=SenderId(uuid4()),
            content=MessageContent("hi"),
        )


async def test_from_user_rejects_a_sender_who_is_not_the_owner() -> None:
    conversations, messages = FakeConversationRepository(), FakeMessageRepository()
    conversation_id = await _conversation_owned_by(conversations, OwnerId(uuid4()))

    with pytest.raises(NotConversationOwner):
        await PostMessage(conversations, messages, FixedClock(_NOW)).from_user(
            conversation_id=conversation_id,
            sender_id=SenderId(uuid4()),
            content=MessageContent("hijack"),
        )


async def test_from_assistant_persists_an_assistant_message_without_a_sender() -> None:
    conversations, messages = FakeConversationRepository(), FakeMessageRepository()
    conversation_id = await _conversation_owned_by(conversations, OwnerId(uuid4()))

    message = await PostMessage(conversations, messages, FixedClock(_NOW)).from_assistant(
        conversation_id=conversation_id,
        content=MessageContent("echo"),
    )

    assert message.role is MessageRole.ASSISTANT
    assert message.sender_id is None
    assert message.content.value == "echo"
