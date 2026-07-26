"""Tests for the RemoveParticipant use case."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from dizzchat.contexts.messaging.application.services import (
    AddParticipant,
    CreateConversation,
    RemoveParticipant,
)
from dizzchat.contexts.messaging.domain.conversation import (
    CannotRemoveConversationOwner,
    Conversation,
    ConversationId,
    ConversationNotFound,
    NotConversationOwner,
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
_GUEST_EMAIL = "guest@example.com"


async def _room_with_a_guest(
    conversations: FakeConversationRepository, owner: OwnerId, guest: UUID
) -> Conversation:
    clock = FixedClock(_NOW)
    created = await CreateConversation(conversations, clock).execute(owner_id=owner, title="room")
    await AddParticipant(conversations, FakeUserDirectory({_GUEST_EMAIL: guest}), clock).execute(
        conversation_id=created.id, owner_id=owner, email=_GUEST_EMAIL
    )
    return created


async def test_the_owner_can_remove_a_participant() -> None:
    conversations = FakeConversationRepository()
    owner, guest = OwnerId(uuid4()), uuid4()
    created = await _room_with_a_guest(conversations, owner, guest)

    await RemoveParticipant(conversations).execute(
        conversation_id=created.id,
        actor_id=ParticipantId(owner.value),
        participant_id=ParticipantId(guest),
    )

    participants = await conversations.list_participants(created.id)
    assert [p.id for p in participants] == [ParticipantId(owner.value)]


async def test_a_participant_can_remove_themselves() -> None:
    conversations = FakeConversationRepository()
    owner, guest = OwnerId(uuid4()), uuid4()
    created = await _room_with_a_guest(conversations, owner, guest)

    await RemoveParticipant(conversations).execute(
        conversation_id=created.id,
        actor_id=ParticipantId(guest),
        participant_id=ParticipantId(guest),
    )

    participants = await conversations.list_participants(created.id)
    assert [p.id for p in participants] == [ParticipantId(owner.value)]


async def test_a_third_party_cannot_remove_someone_else() -> None:
    conversations = FakeConversationRepository()
    owner, guest = OwnerId(uuid4()), uuid4()
    created = await _room_with_a_guest(conversations, owner, guest)

    with pytest.raises(NotConversationOwner):
        await RemoveParticipant(conversations).execute(
            conversation_id=created.id,
            actor_id=ParticipantId(uuid4()),
            participant_id=ParticipantId(guest),
        )


async def test_the_owner_cannot_be_removed() -> None:
    conversations = FakeConversationRepository()
    owner = OwnerId(uuid4())
    created = await _room_with_a_guest(conversations, owner, uuid4())

    with pytest.raises(CannotRemoveConversationOwner):
        await RemoveParticipant(conversations).execute(
            conversation_id=created.id,
            actor_id=ParticipantId(owner.value),
            participant_id=ParticipantId(owner.value),
        )


async def test_removing_someone_who_never_joined_is_rejected() -> None:
    conversations = FakeConversationRepository()
    owner = OwnerId(uuid4())
    created = await CreateConversation(conversations, FixedClock(_NOW)).execute(
        owner_id=owner, title="room"
    )

    with pytest.raises(NotConversationParticipant):
        await RemoveParticipant(conversations).execute(
            conversation_id=created.id,
            actor_id=ParticipantId(owner.value),
            participant_id=ParticipantId(uuid4()),
        )


async def test_a_missing_conversation_is_rejected() -> None:
    participant = ParticipantId(uuid4())
    with pytest.raises(ConversationNotFound):
        await RemoveParticipant(FakeConversationRepository()).execute(
            conversation_id=ConversationId(uuid4()),
            actor_id=participant,
            participant_id=participant,
        )
