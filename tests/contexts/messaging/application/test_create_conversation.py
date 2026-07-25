"""Tests for the CreateConversation use case."""

from datetime import UTC, datetime
from uuid import uuid4

from dizzchat.contexts.messaging.application.services import CreateConversation
from dizzchat.contexts.messaging.domain.conversation import OwnerId
from tests.contexts.messaging.fakes import FakeConversationRepository, FixedClock

_NOW = datetime(2024, 1, 1, tzinfo=UTC)


async def test_creates_and_persists_a_conversation_with_a_normalized_title() -> None:
    conversations = FakeConversationRepository()
    owner = OwnerId(uuid4())
    handler = CreateConversation(conversations, FixedClock(_NOW))

    conversation = await handler.execute(owner_id=owner, title="  Planning  ")

    assert conversation.title.value == "Planning"
    assert conversation.owner_id == owner
    assert conversation.created_at == _NOW
    assert conversation.updated_at == _NOW
    listed = await conversations.list_for_owner(owner)
    assert [c.id for c in listed] == [conversation.id]
