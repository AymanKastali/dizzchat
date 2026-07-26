"""Repository port for the Conversation aggregate root.

Declared in the domain (the aggregate owns the contract for its own persistence); the concrete
adapter lives in ``infrastructure/outbound/persistence``. Write methods take attributes rather
than the aggregate, so the persistence layer cannot mutate domain state. Soft-deleted
conversations are treated as absent — ``get`` and ``list_for_owner`` never return them.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from dizzchat.contexts.messaging.domain.conversation.conversation import Conversation
from dizzchat.contexts.messaging.domain.conversation.participant import Participant
from dizzchat.contexts.messaging.domain.conversation.value_objects import (
    ConversationId,
    ConversationTitle,
    OwnerId,
    ParticipantId,
)


class ConversationRepository(Protocol):
    """Persistence for the ``Conversation`` aggregate root."""

    async def create(
        self,
        *,
        conversation_id: ConversationId,
        owner_id: OwnerId,
        title: ConversationTitle,
        created_at: datetime,
        updated_at: datetime,
        participant_ids: frozenset[ParticipantId],
    ) -> None:
        """Persist a newly started conversation together with its initial participants.

        Taking the participants here (rather than leaving the caller to add them afterwards) keeps
        the aggregate's "the owner is a participant" invariant intact across persistence.
        """
        ...

    async def update(
        self,
        *,
        conversation_id: ConversationId,
        title: ConversationTitle,
        updated_at: datetime,
        deleted_at: datetime | None,
    ) -> None:
        """Persist the mutable state of an existing conversation (rename, soft-delete)."""
        ...

    async def get(self, conversation_id: ConversationId) -> Conversation | None:
        """Return the active conversation with this id (participants included), or ``None``."""
        ...

    async def list_for_participant(self, participant_id: ParticipantId) -> list[Conversation]:
        """Return the active conversations this user takes part in, newest first.

        Includes the ones they own, since an owner is always a participant.
        """
        ...

    async def add_participant(
        self,
        *,
        conversation_id: ConversationId,
        participant_id: ParticipantId,
        joined_at: datetime,
    ) -> None:
        """Record a new membership. The caller has already checked it does not exist."""
        ...

    async def remove_participant(
        self, *, conversation_id: ConversationId, participant_id: ParticipantId
    ) -> None:
        """Drop a membership."""
        ...

    async def list_participants(self, conversation_id: ConversationId) -> list[Participant]:
        """Return the conversation's memberships with their join times, oldest first."""
        ...
