"""Tests for the RestoreConversation use case (undo a soft-delete)."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from dizzchat.contexts.messaging.application.services import (
    CreateConversation,
    DeleteConversation,
    ListConversations,
    RestoreConversation,
)
from dizzchat.contexts.messaging.domain.conversation import (
    ConversationId,
    ConversationNotFound,
    NotConversationOwner,
    OwnerId,
    ParticipantId,
)
from tests.contexts.messaging.fakes import FakeConversationRepository, FixedClock

_NOW = datetime(2024, 1, 1, tzinfo=UTC)
_LATER = _NOW + timedelta(hours=1)


async def test_restores_a_deleted_conversation_the_caller_owns() -> None:
    conversations = FakeConversationRepository()
    owner = OwnerId(uuid4())
    created = await CreateConversation(conversations, FixedClock(_NOW)).execute(
        owner_id=owner, title="x"
    )
    await DeleteConversation(conversations, FixedClock(_NOW)).execute(
        conversation_id=created.id, owner_id=owner
    )

    restored = await RestoreConversation(conversations, FixedClock(_LATER)).execute(
        conversation_id=created.id, owner_id=owner
    )

    assert restored.is_deleted is False
    assert restored.updated_at == _LATER
    assert await conversations.get(created.id) is not None


async def test_a_restored_conversation_is_listed_again() -> None:
    conversations = FakeConversationRepository()
    owner = OwnerId(uuid4())
    created = await CreateConversation(conversations, FixedClock(_NOW)).execute(
        owner_id=owner, title="x"
    )
    await DeleteConversation(conversations, FixedClock(_NOW)).execute(
        conversation_id=created.id, owner_id=owner
    )
    assert (
        await ListConversations(conversations).execute(participant_id=ParticipantId(owner.value))
        == []
    )

    await RestoreConversation(conversations, FixedClock(_LATER)).execute(
        conversation_id=created.id, owner_id=owner
    )

    listed = await ListConversations(conversations).execute(
        participant_id=ParticipantId(owner.value)
    )
    assert [c.id for c in listed] == [created.id]


async def test_restoring_an_active_conversation_succeeds_without_changing_it() -> None:
    conversations = FakeConversationRepository()
    owner = OwnerId(uuid4())
    created = await CreateConversation(conversations, FixedClock(_NOW)).execute(
        owner_id=owner, title="x"
    )

    restored = await RestoreConversation(conversations, FixedClock(_LATER)).execute(
        conversation_id=created.id, owner_id=owner
    )

    assert restored.is_deleted is False
    assert restored.updated_at == _NOW  # untouched, so a repeated restore is idempotent


async def test_rejects_restoring_a_conversation_owned_by_another() -> None:
    conversations = FakeConversationRepository()
    owner = OwnerId(uuid4())
    created = await CreateConversation(conversations, FixedClock(_NOW)).execute(
        owner_id=owner, title="x"
    )
    await DeleteConversation(conversations, FixedClock(_NOW)).execute(
        conversation_id=created.id, owner_id=owner
    )

    with pytest.raises(NotConversationOwner):
        await RestoreConversation(conversations, FixedClock(_LATER)).execute(
            conversation_id=created.id, owner_id=OwnerId(uuid4())
        )


async def test_rejects_restoring_a_conversation_that_never_existed() -> None:
    with pytest.raises(ConversationNotFound):
        await RestoreConversation(FakeConversationRepository(), FixedClock(_NOW)).execute(
            conversation_id=ConversationId(uuid4()), owner_id=OwnerId(uuid4())
        )
