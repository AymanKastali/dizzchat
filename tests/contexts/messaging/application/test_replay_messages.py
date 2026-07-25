"""Tests for the ReplayMessages use case (reconnect replay, oldest-first, id above the cursor)."""

from datetime import UTC, datetime
from uuid import uuid4

from dizzchat.contexts.messaging.application.services import ReplayMessages
from dizzchat.contexts.messaging.domain.conversation import ConversationId
from dizzchat.contexts.messaging.domain.message import (
    MessageContent,
    MessageId,
    MessageRole,
    SenderId,
)
from tests.contexts.messaging.fakes import FakeMessageRepository

_NOW = datetime(2024, 1, 1, tzinfo=UTC)


async def _seed_three(messages: FakeMessageRepository, conversation_id: ConversationId) -> None:
    for _ in range(3):
        await messages.create(
            conversation_id=conversation_id,
            sender_id=SenderId(uuid4()),
            role=MessageRole.USER,
            content=MessageContent("hi"),
            created_at=_NOW,
        )


async def test_replay_returns_messages_after_the_cursor_in_ascending_order() -> None:
    messages = FakeMessageRepository()
    conversation_id = ConversationId(uuid4())
    await _seed_three(messages, conversation_id)

    result = await ReplayMessages(messages).execute(
        conversation_id=conversation_id, after=MessageId(1)
    )

    assert [m.id.value for m in result] == [2, 3]


async def test_replay_after_the_latest_seq_is_empty() -> None:
    messages = FakeMessageRepository()
    conversation_id = ConversationId(uuid4())
    await _seed_three(messages, conversation_id)

    result = await ReplayMessages(messages).execute(
        conversation_id=conversation_id, after=MessageId(3)
    )

    assert result == []


async def test_replay_with_no_cursor_returns_all() -> None:
    messages = FakeMessageRepository()
    conversation_id = ConversationId(uuid4())
    await _seed_three(messages, conversation_id)

    result = await ReplayMessages(messages).execute(conversation_id=conversation_id, after=None)

    assert [m.id.value for m in result] == [1, 2, 3]
