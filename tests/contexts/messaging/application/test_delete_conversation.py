"""Tests for the DeleteConversation use case (soft-delete)."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from dizzchat.contexts.messaging.application.services import (
    CreateConversation,
    DeleteConversation,
)
from dizzchat.contexts.messaging.domain.conversation import (
    ConversationId,
    ConversationNotFound,
    NotConversationOwner,
    OwnerId,
)
from tests.contexts.messaging.fakes import FakeConversationRepository, FixedClock

_NOW = datetime(2024, 1, 1, tzinfo=UTC)


async def test_soft_deletes_a_conversation_the_caller_owns() -> None:
    conversations = FakeConversationRepository()
    owner = OwnerId(uuid4())
    created = await CreateConversation(conversations, FixedClock(_NOW)).execute(
        owner_id=owner, title="x"
    )

    await DeleteConversation(conversations, FixedClock(_NOW)).execute(
        conversation_id=created.id, owner_id=owner
    )

    assert await conversations.get(created.id) is None


async def test_rejects_deleting_a_missing_conversation() -> None:
    with pytest.raises(ConversationNotFound):
        await DeleteConversation(FakeConversationRepository(), FixedClock(_NOW)).execute(
            conversation_id=ConversationId(uuid4()), owner_id=OwnerId(uuid4())
        )


async def test_rejects_deleting_a_conversation_owned_by_another() -> None:
    conversations = FakeConversationRepository()
    created = await CreateConversation(conversations, FixedClock(_NOW)).execute(
        owner_id=OwnerId(uuid4()), title="x"
    )

    with pytest.raises(NotConversationOwner):
        await DeleteConversation(conversations, FixedClock(_NOW)).execute(
            conversation_id=created.id, owner_id=OwnerId(uuid4())
        )
