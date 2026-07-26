"""Tests for the AddParticipant use case."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from dizzchat.contexts.messaging.application.services import AddParticipant, CreateConversation
from dizzchat.contexts.messaging.domain.conversation import (
    ConversationId,
    ConversationNotFound,
    NotConversationOwner,
    OwnerId,
    ParticipantId,
    ParticipantUserNotFound,
)
from tests.contexts.messaging.fakes import (
    FakeConversationRepository,
    FakeUserDirectory,
    FixedClock,
)

_NOW = datetime(2024, 1, 1, tzinfo=UTC)
_GUEST_EMAIL = "guest@example.com"


async def test_the_owner_can_admit_a_registered_user() -> None:
    conversations = FakeConversationRepository()
    clock = FixedClock(_NOW)
    owner = OwnerId(uuid4())
    created = await CreateConversation(conversations, clock).execute(owner_id=owner, title="room")
    guest = uuid4()

    admitted = await AddParticipant(
        conversations, FakeUserDirectory({_GUEST_EMAIL: guest}), clock
    ).execute(conversation_id=created.id, owner_id=owner, email=_GUEST_EMAIL)

    assert admitted == ParticipantId(guest)
    participants = await conversations.list_participants(created.id)
    assert {p.id for p in participants} == {ParticipantId(owner.value), ParticipantId(guest)}


async def test_re_inviting_an_existing_participant_writes_nothing() -> None:
    conversations = FakeConversationRepository()
    clock = FixedClock(_NOW)
    owner = OwnerId(uuid4())
    created = await CreateConversation(conversations, clock).execute(owner_id=owner, title="room")
    handler = AddParticipant(conversations, FakeUserDirectory({_GUEST_EMAIL: uuid4()}), clock)

    await handler.execute(conversation_id=created.id, owner_id=owner, email=_GUEST_EMAIL)
    await handler.execute(conversation_id=created.id, owner_id=owner, email=_GUEST_EMAIL)

    assert len(await conversations.list_participants(created.id)) == 2


async def test_only_the_owner_may_invite() -> None:
    conversations = FakeConversationRepository()
    clock = FixedClock(_NOW)
    created = await CreateConversation(conversations, clock).execute(
        owner_id=OwnerId(uuid4()), title="room"
    )

    with pytest.raises(NotConversationOwner):
        await AddParticipant(
            conversations, FakeUserDirectory({_GUEST_EMAIL: uuid4()}), clock
        ).execute(conversation_id=created.id, owner_id=OwnerId(uuid4()), email=_GUEST_EMAIL)


async def test_an_unregistered_email_is_rejected() -> None:
    conversations = FakeConversationRepository()
    clock = FixedClock(_NOW)
    owner = OwnerId(uuid4())
    created = await CreateConversation(conversations, clock).execute(owner_id=owner, title="room")

    with pytest.raises(ParticipantUserNotFound):
        await AddParticipant(conversations, FakeUserDirectory(), clock).execute(
            conversation_id=created.id, owner_id=owner, email="nobody@example.com"
        )


async def test_a_missing_conversation_is_rejected() -> None:
    with pytest.raises(ConversationNotFound):
        await AddParticipant(
            FakeConversationRepository(),
            FakeUserDirectory({_GUEST_EMAIL: uuid4()}),
            FixedClock(_NOW),
        ).execute(
            conversation_id=ConversationId(uuid4()),
            owner_id=OwnerId(uuid4()),
            email=_GUEST_EMAIL,
        )
