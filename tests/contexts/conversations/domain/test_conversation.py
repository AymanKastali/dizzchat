"""Unit tests for the Conversation aggregate root — lifecycle and ownership."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from dizzchat.contexts.conversations.domain.conversation import (
    Conversation,
    ConversationId,
    ConversationTitle,
    NotConversationOwner,
    OwnerId,
)

_CREATED_AT = datetime(2024, 1, 1, tzinfo=UTC)


def _conversation(*, owner: OwnerId | None = None, raw_id: UUID | None = None) -> Conversation:
    return Conversation.start(
        conversation_id=ConversationId(raw_id or uuid4()),
        owner_id=owner or OwnerId(uuid4()),
        title=ConversationTitle("Planning"),
        created_at=_CREATED_AT,
    )


def test_start_sets_created_and_updated_to_the_same_instant() -> None:
    conversation = _conversation()

    assert conversation.created_at == _CREATED_AT
    assert conversation.updated_at == _CREATED_AT
    assert conversation.is_deleted is False


def test_rename_changes_the_title_and_bumps_updated_at() -> None:
    conversation = _conversation()
    later = _CREATED_AT + timedelta(hours=1)

    conversation.rename(new_title=ConversationTitle("Roadmap"), now=later)

    assert conversation.title == ConversationTitle("Roadmap")
    assert conversation.updated_at == later


def test_delete_soft_deletes_and_is_idempotent() -> None:
    conversation = _conversation()
    deleted_at = _CREATED_AT + timedelta(hours=1)

    conversation.delete(deleted_at)
    assert conversation.is_deleted is True
    assert conversation.deleted_at == deleted_at

    # Re-deleting keeps the original timestamp.
    conversation.delete(deleted_at + timedelta(hours=1))
    assert conversation.deleted_at == deleted_at


def test_ensure_owned_by_accepts_the_owner_and_rejects_others() -> None:
    owner = OwnerId(uuid4())
    conversation = _conversation(owner=owner)

    conversation.ensure_owned_by(owner)  # does not raise

    with pytest.raises(NotConversationOwner):
        conversation.ensure_owned_by(OwnerId(uuid4()))


def test_conversations_are_equal_by_identity_not_fields() -> None:
    raw_id = uuid4()

    assert _conversation(raw_id=raw_id) == _conversation(raw_id=raw_id)
    assert _conversation(raw_id=raw_id) != _conversation(raw_id=uuid4())
