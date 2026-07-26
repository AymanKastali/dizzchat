"""Tests for the ListParticipants use case."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from dizzchat.contexts.messaging.application.services import (
    AddParticipant,
    CreateConversation,
    ListParticipants,
)
from dizzchat.contexts.messaging.domain.conversation import (
    ConversationId,
    ConversationNotFound,
    NotConversationParticipant,
    OwnerId,
    ParticipantId,
)
from tests.contexts.messaging.fakes import (
    FakeConversationRepository,
    FakeUserDirectory,
    FixedClock,
)

_NOW = datetime(2024, 1, 1, tzinfo=UTC)


async def test_lists_memberships_oldest_first_for_a_participant() -> None:
    conversations = FakeConversationRepository()
    owner = OwnerId(uuid4())
    created = await CreateConversation(conversations, FixedClock(_NOW)).execute(
        owner_id=owner, title="room"
    )
    guest = uuid4()
    later = _NOW + timedelta(minutes=5)
    await AddParticipant(
        conversations, FakeUserDirectory({"guest@example.com": guest}), FixedClock(later)
    ).execute(conversation_id=created.id, owner_id=owner, email="guest@example.com")

    listed = await ListParticipants(conversations).execute(
        conversation_id=created.id, participant_id=ParticipantId(guest)
    )

    assert [p.id for p in listed] == [ParticipantId(owner.value), ParticipantId(guest)]
    assert [p.joined_at for p in listed] == [_NOW, later]


async def test_rejects_someone_who_is_not_a_participant() -> None:
    conversations = FakeConversationRepository()
    created = await CreateConversation(conversations, FixedClock(_NOW)).execute(
        owner_id=OwnerId(uuid4()), title="room"
    )

    with pytest.raises(NotConversationParticipant):
        await ListParticipants(conversations).execute(
            conversation_id=created.id, participant_id=ParticipantId(uuid4())
        )


async def test_rejects_a_missing_conversation() -> None:
    with pytest.raises(ConversationNotFound):
        await ListParticipants(FakeConversationRepository()).execute(
            conversation_id=ConversationId(uuid4()), participant_id=ParticipantId(uuid4())
        )
