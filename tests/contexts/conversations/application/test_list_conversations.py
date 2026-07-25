"""Tests for the ListConversations use case."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from dizzchat.contexts.conversations.application.services import (
    CreateConversation,
    DeleteConversation,
    ListConversations,
)
from dizzchat.contexts.conversations.domain.conversation import Conversation, OwnerId
from tests.contexts.conversations.fakes import FakeConversationRepository, FixedClock

_NOW = datetime(2024, 1, 1, tzinfo=UTC)


async def _create(
    conversations: FakeConversationRepository, owner: OwnerId, title: str, at: datetime
) -> Conversation:
    handler = CreateConversation(conversations, FixedClock(at))
    return await handler.execute(owner_id=owner, title=title)


async def test_lists_only_the_owners_active_conversations_newest_first() -> None:
    conversations = FakeConversationRepository()
    owner = OwnerId(uuid4())
    other = OwnerId(uuid4())

    first = await _create(conversations, owner, "first", _NOW)
    second = await _create(conversations, owner, "second", _NOW + timedelta(minutes=1))
    await _create(conversations, other, "theirs", _NOW)

    listed = await ListConversations(conversations).execute(owner_id=owner)

    assert [c.id for c in listed] == [second.id, first.id]


async def test_excludes_soft_deleted_conversations() -> None:
    conversations = FakeConversationRepository()
    owner = OwnerId(uuid4())
    kept = await _create(conversations, owner, "keep", _NOW)
    dropped = await _create(conversations, owner, "drop", _NOW + timedelta(minutes=1))
    await DeleteConversation(conversations, FixedClock(_NOW)).execute(
        conversation_id=dropped.id, owner_id=owner
    )

    listed = await ListConversations(conversations).execute(owner_id=owner)

    assert [c.id for c in listed] == [kept.id]
