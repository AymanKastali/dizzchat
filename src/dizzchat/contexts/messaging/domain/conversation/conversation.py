"""Conversation aggregate root."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from dizzchat.contexts.messaging.domain.conversation.errors import (
    CannotRemoveConversationOwner,
    NotConversationOwner,
    NotConversationParticipant,
)
from dizzchat.contexts.messaging.domain.conversation.value_objects import (
    ConversationId,
    ConversationTitle,
    OwnerId,
    ParticipantId,
)


@dataclass(eq=False, slots=True)
class Conversation:
    """A conversation between its participants, identified by ``id``.

    Owns its lifecycle — started by an owner (:meth:`start`), retitled (:meth:`rename`),
    soft-deleted (:meth:`delete`) and restored (:meth:`restore`) — and its membership: who may
    read and post
    (:meth:`ensure_participant`) versus who may administer it (:meth:`ensure_owned_by`). The owner
    is a participant from the moment the conversation starts, and cannot be removed.

    ``participant_ids`` holds identities only. ``joined_at`` lives on the ``Participant``
    projection instead, because no invariant here depends on it. An entity: equal by identity.
    """

    id: ConversationId
    owner_id: OwnerId
    title: ConversationTitle
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None
    participant_ids: frozenset[ParticipantId] = frozenset()

    @classmethod
    def start(
        cls,
        *,
        conversation_id: ConversationId,
        owner_id: OwnerId,
        title: ConversationTitle,
        created_at: datetime,
    ) -> Conversation:
        """Start a new conversation owned by ``owner_id``, who is also its first participant."""
        return cls(
            id=conversation_id,
            owner_id=owner_id,
            title=title,
            created_at=created_at,
            updated_at=created_at,
            participant_ids=frozenset({ParticipantId(owner_id.value)}),
        )

    def rename(self, *, new_title: ConversationTitle, now: datetime) -> None:
        """Change the title, recording the modification time."""
        self.title = new_title
        self.updated_at = now

    def delete(self, now: datetime) -> None:
        """Soft-delete. Idempotent — re-deleting keeps the original timestamp."""
        if self.deleted_at is None:
            self.deleted_at = now
            self.updated_at = now

    def restore(self, now: datetime) -> None:
        """Undo a soft-delete. Idempotent — restoring an active conversation changes nothing.

        Deliberately symmetric with :meth:`delete`: both are no-ops in the state they lead to, so a
        retried request cannot corrupt ``updated_at``.
        """
        if self.deleted_at is not None:
            self.deleted_at = None
            self.updated_at = now

    def ensure_owned_by(self, owner_id: OwnerId) -> None:
        """Guard that ``owner_id`` owns this conversation, else raise ``NotConversationOwner``."""
        if self.owner_id != owner_id:
            raise NotConversationOwner()

    def is_participant(self, participant_id: ParticipantId) -> bool:
        """Whether this user takes part in the conversation (the owner always does)."""
        return participant_id in self.participant_ids

    def ensure_participant(self, participant_id: ParticipantId) -> None:
        """Guard that this user may read and post here, else raise ``NotConversationParticipant``.

        The authorization rule for joining the live channel, sending, and reading history — as
        opposed to :meth:`ensure_owned_by`, which guards administration.
        """
        if not self.is_participant(participant_id):
            raise NotConversationParticipant()

    def add_participant(self, participant_id: ParticipantId) -> bool:
        """Admit a user. Idempotent — returns ``True`` only when the membership is new."""
        if self.is_participant(participant_id):
            return False
        self.participant_ids = self.participant_ids | {participant_id}
        return True

    def remove_participant(self, participant_id: ParticipantId) -> None:
        """Remove a participant, refusing to remove the owner.

        Raises ``CannotRemoveConversationOwner`` for the owner and ``NotConversationParticipant``
        for someone who was never a member, so a caller can tell a refusal from a no-op.
        """
        if participant_id.value == self.owner_id.value:
            raise CannotRemoveConversationOwner()
        if not self.is_participant(participant_id):
            raise NotConversationParticipant()
        self.participant_ids = self.participant_ids - {participant_id}

    @property
    def is_deleted(self) -> bool:
        """True once the conversation has been soft-deleted."""
        return self.deleted_at is not None

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Conversation) and other.id == self.id

    def __hash__(self) -> int:
        return hash(self.id)
