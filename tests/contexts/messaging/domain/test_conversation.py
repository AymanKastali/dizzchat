"""Unit tests for the Conversation aggregate root — lifecycle, ownership, and membership."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from dizzchat.contexts.messaging.domain.conversation import (
    CannotRemoveConversationOwner,
    Conversation,
    ConversationId,
    ConversationTitle,
    NotConversationOwner,
    NotConversationParticipant,
    OwnerId,
    ParticipantId,
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


def test_restore_clears_the_deletion_and_bumps_updated_at() -> None:
    conversation = _conversation()
    deleted_at = _CREATED_AT + timedelta(hours=1)
    restored_at = _CREATED_AT + timedelta(hours=2)
    conversation.delete(deleted_at)

    conversation.restore(restored_at)

    assert conversation.is_deleted is False
    assert conversation.deleted_at is None
    assert conversation.updated_at == restored_at


def test_restoring_an_active_conversation_changes_nothing() -> None:
    conversation = _conversation()

    conversation.restore(_CREATED_AT + timedelta(hours=1))

    assert conversation.is_deleted is False
    assert conversation.updated_at == _CREATED_AT  # not bumped — a no-op, so a retry is harmless


def test_a_restored_conversation_can_be_deleted_again() -> None:
    conversation = _conversation()
    second_deletion = _CREATED_AT + timedelta(hours=3)
    conversation.delete(_CREATED_AT + timedelta(hours=1))
    conversation.restore(_CREATED_AT + timedelta(hours=2))

    conversation.delete(second_deletion)

    assert conversation.deleted_at == second_deletion


def test_restore_keeps_the_participants() -> None:
    owner = OwnerId(uuid4())
    conversation = _conversation(owner=owner)
    guest = ParticipantId(uuid4())
    conversation.add_participant(guest)
    conversation.delete(_CREATED_AT + timedelta(hours=1))

    conversation.restore(_CREATED_AT + timedelta(hours=2))

    assert conversation.participant_ids == frozenset({ParticipantId(owner.value), guest})


def test_ensure_owned_by_accepts_the_owner_and_rejects_others() -> None:
    owner = OwnerId(uuid4())
    conversation = _conversation(owner=owner)

    conversation.ensure_owned_by(owner)  # does not raise

    with pytest.raises(NotConversationOwner):
        conversation.ensure_owned_by(OwnerId(uuid4()))


def test_start_makes_the_owner_the_first_participant() -> None:
    owner = OwnerId(uuid4())
    conversation = _conversation(owner=owner)

    assert conversation.participant_ids == frozenset({ParticipantId(owner.value)})
    conversation.ensure_participant(ParticipantId(owner.value))  # does not raise


def test_ensure_participant_accepts_members_and_rejects_everyone_else() -> None:
    conversation = _conversation()
    guest = ParticipantId(uuid4())

    with pytest.raises(NotConversationParticipant):
        conversation.ensure_participant(guest)

    conversation.add_participant(guest)
    conversation.ensure_participant(guest)  # does not raise


def test_add_participant_is_idempotent() -> None:
    conversation = _conversation()
    guest = ParticipantId(uuid4())

    assert conversation.add_participant(guest) is True
    assert conversation.add_participant(guest) is False
    assert len(conversation.participant_ids) == 2  # owner + one guest, not two guest entries


def test_remove_participant_drops_a_member() -> None:
    owner = OwnerId(uuid4())
    conversation = _conversation(owner=owner)
    guest = ParticipantId(uuid4())
    conversation.add_participant(guest)

    conversation.remove_participant(guest)

    assert conversation.participant_ids == frozenset({ParticipantId(owner.value)})


def test_remove_participant_refuses_the_owner() -> None:
    owner = OwnerId(uuid4())
    conversation = _conversation(owner=owner)

    with pytest.raises(CannotRemoveConversationOwner):
        conversation.remove_participant(ParticipantId(owner.value))


def test_remove_participant_rejects_someone_who_never_joined() -> None:
    conversation = _conversation()

    with pytest.raises(NotConversationParticipant):
        conversation.remove_participant(ParticipantId(uuid4()))


def test_conversations_are_equal_by_identity_not_fields() -> None:
    raw_id = uuid4()

    assert _conversation(raw_id=raw_id) == _conversation(raw_id=raw_id)
    assert _conversation(raw_id=raw_id) != _conversation(raw_id=uuid4())
