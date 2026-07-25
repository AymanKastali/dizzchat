"""Tests for the GetConversationHistory use case (cursor pagination)."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from dizzchat.contexts.messaging.application.services import (
    CreateConversation,
    GetConversationHistory,
)
from dizzchat.contexts.messaging.domain.conversation import (
    Conversation,
    ConversationId,
    ConversationNotFound,
    NotConversationOwner,
    OwnerId,
)
from dizzchat.contexts.messaging.domain.message import MessageContent, SenderId
from tests.contexts.messaging.fakes import (
    FakeConversationRepository,
    FakeMessageRepository,
    FixedClock,
)

_NOW = datetime(2024, 1, 1, tzinfo=UTC)


async def _seed(
    conversations: FakeConversationRepository,
    messages: FakeMessageRepository,
    owner: OwnerId,
    count: int,
) -> Conversation:
    created = await CreateConversation(conversations, FixedClock(_NOW)).execute(
        owner_id=owner, title="c"
    )
    sender = SenderId(owner.value)
    for i in range(count):
        await messages.create(
            conversation_id=created.id,
            sender_id=sender,
            content=MessageContent(f"m{i}"),
            created_at=_NOW,
        )
    return created


async def test_returns_latest_page_with_more_flag_and_cursor() -> None:
    conversations, messages = FakeConversationRepository(), FakeMessageRepository()
    owner = OwnerId(uuid4())
    created = await _seed(conversations, messages, owner, count=5)

    page = await GetConversationHistory(conversations, messages).execute(
        conversation_id=created.id, owner_id=owner, before=None, limit=3
    )

    # Newest first (ids 5,4,3); older messages remain, so the cursor is the oldest returned (3).
    assert [m.content.value for m in page.items] == ["m4", "m3", "m2"]
    assert page.has_more is True
    assert page.next_cursor is not None
    assert page.next_cursor.value == 3


async def test_second_page_via_before_cursor_returns_older_messages() -> None:
    conversations, messages = FakeConversationRepository(), FakeMessageRepository()
    owner = OwnerId(uuid4())
    created = await _seed(conversations, messages, owner, count=5)
    handler = GetConversationHistory(conversations, messages)
    first = await handler.execute(conversation_id=created.id, owner_id=owner, before=None, limit=3)

    second = await handler.execute(
        conversation_id=created.id, owner_id=owner, before=first.next_cursor, limit=3
    )

    assert [m.content.value for m in second.items] == ["m1", "m0"]
    assert second.has_more is False
    assert second.next_cursor is None


async def test_rejects_history_for_a_missing_conversation() -> None:
    handler = GetConversationHistory(FakeConversationRepository(), FakeMessageRepository())
    with pytest.raises(ConversationNotFound):
        await handler.execute(
            conversation_id=ConversationId(uuid4()),
            owner_id=OwnerId(uuid4()),
            before=None,
            limit=10,
        )


async def test_rejects_history_for_a_conversation_owned_by_another() -> None:
    conversations, messages = FakeConversationRepository(), FakeMessageRepository()
    created = await _seed(conversations, messages, OwnerId(uuid4()), count=1)

    with pytest.raises(NotConversationOwner):
        await GetConversationHistory(conversations, messages).execute(
            conversation_id=created.id, owner_id=OwnerId(uuid4()), before=None, limit=10
        )
