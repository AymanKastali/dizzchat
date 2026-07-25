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
from dizzchat.contexts.messaging.domain.conversation.value_objects import (
    ConversationId,
    ConversationTitle,
    OwnerId,
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
    ) -> None:
        """Persist a newly started conversation."""
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
        """Return the active conversation with this id, or ``None`` if absent or deleted."""
        ...

    async def list_for_owner(self, owner_id: OwnerId) -> list[Conversation]:
        """Return the owner's active conversations, newest first."""
        ...
