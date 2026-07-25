"""Tests for the EnsureConversationAccess use case."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from dizzchat.contexts.messaging.application.services import (
    CreateConversation,
    EnsureConversationAccess,
)
from dizzchat.contexts.messaging.domain.conversation import (
    ConversationId,
    ConversationNotFound,
    NotConversationOwner,
    OwnerId,
)
from tests.contexts.messaging.fakes import FakeConversationRepository, FixedClock

_NOW = datetime(2024, 1, 1, tzinfo=UTC)


async def test_allows_the_owner() -> None:
    conversations = FakeConversationRepository()
    owner = OwnerId(uuid4())
    created = await CreateConversation(conversations, FixedClock(_NOW)).execute(
        owner_id=owner, title="c"
    )

    await EnsureConversationAccess(conversations).execute(
        conversation_id=created.id, owner_id=owner
    )  # does not raise


async def test_rejects_a_non_owner() -> None:
    conversations = FakeConversationRepository()
    created = await CreateConversation(conversations, FixedClock(_NOW)).execute(
        owner_id=OwnerId(uuid4()), title="c"
    )

    with pytest.raises(NotConversationOwner):
        await EnsureConversationAccess(conversations).execute(
            conversation_id=created.id, owner_id=OwnerId(uuid4())
        )


async def test_rejects_a_missing_conversation() -> None:
    with pytest.raises(ConversationNotFound):
        await EnsureConversationAccess(FakeConversationRepository()).execute(
            conversation_id=ConversationId(uuid4()), owner_id=OwnerId(uuid4())
        )
