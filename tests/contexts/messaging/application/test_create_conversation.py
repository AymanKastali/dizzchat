"""Tests for the CreateConversation use case."""

from datetime import UTC, datetime
from uuid import uuid4

from dizzchat.contexts.messaging.application.services import CreateConversation
from dizzchat.contexts.messaging.domain.conversation import OwnerId, ParticipantId
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
    listed = await conversations.list_for_participant(ParticipantId(owner.value))
    assert [c.id for c in listed] == [conversation.id]


async def test_persists_the_owner_as_the_first_participant() -> None:
    conversations = FakeConversationRepository()
    owner = OwnerId(uuid4())

    conversation = await CreateConversation(conversations, FixedClock(_NOW)).execute(
        owner_id=owner, title="Planning"
    )

    participants = await conversations.list_participants(conversation.id)
    assert [p.id for p in participants] == [ParticipantId(owner.value)]
    assert participants[0].joined_at == _NOW
