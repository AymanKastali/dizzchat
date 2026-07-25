"""Tests for the RenameConversation use case."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from dizzchat.contexts.conversations.application.services import (
    CreateConversation,
    RenameConversation,
)
from dizzchat.contexts.conversations.domain.conversation import (
    ConversationId,
    ConversationNotFound,
    NotConversationOwner,
    OwnerId,
)
from tests.contexts.conversations.fakes import FakeConversationRepository, FixedClock

_NOW = datetime(2024, 1, 1, tzinfo=UTC)


async def test_renames_a_conversation_the_caller_owns() -> None:
    conversations = FakeConversationRepository()
    owner = OwnerId(uuid4())
    created = await CreateConversation(conversations, FixedClock(_NOW)).execute(
        owner_id=owner, title="old"
    )
    later = _NOW + timedelta(hours=1)

    renamed = await RenameConversation(conversations, FixedClock(later)).execute(
        conversation_id=created.id, owner_id=owner, new_title="new"
    )

    assert renamed.title.value == "new"
    assert renamed.updated_at == later
    reloaded = await conversations.get(created.id)
    assert reloaded is not None and reloaded.title.value == "new"


async def test_rejects_renaming_a_missing_conversation() -> None:
    with pytest.raises(ConversationNotFound):
        await RenameConversation(FakeConversationRepository(), FixedClock(_NOW)).execute(
            conversation_id=ConversationId(uuid4()), owner_id=OwnerId(uuid4()), new_title="x"
        )


async def test_rejects_renaming_a_conversation_owned_by_another() -> None:
    conversations = FakeConversationRepository()
    created = await CreateConversation(conversations, FixedClock(_NOW)).execute(
        owner_id=OwnerId(uuid4()), title="old"
    )

    with pytest.raises(NotConversationOwner):
        await RenameConversation(conversations, FixedClock(_NOW)).execute(
            conversation_id=created.id, owner_id=OwnerId(uuid4()), new_title="x"
        )
