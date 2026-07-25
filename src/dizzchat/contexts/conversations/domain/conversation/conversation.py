"""Conversation aggregate root."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from dizzchat.contexts.conversations.domain.conversation.errors import NotConversationOwner
from dizzchat.contexts.conversations.domain.conversation.value_objects import (
    ConversationId,
    ConversationTitle,
    OwnerId,
)


@dataclass(eq=False, slots=True)
class Conversation:
    """A user's conversation, identified by ``id``.

    Owns its lifecycle — started by an owner (:meth:`start`), retitled (:meth:`rename`), and
    soft-deleted (:meth:`delete`). Ownership is enforced by :meth:`ensure_owned_by`. An entity:
    equal by identity.
    """

    id: ConversationId
    owner_id: OwnerId
    title: ConversationTitle
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None

    @classmethod
    def start(
        cls,
        *,
        conversation_id: ConversationId,
        owner_id: OwnerId,
        title: ConversationTitle,
        created_at: datetime,
    ) -> Conversation:
        """Start a new conversation owned by ``owner_id``."""
        return cls(
            id=conversation_id,
            owner_id=owner_id,
            title=title,
            created_at=created_at,
            updated_at=created_at,
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

    def ensure_owned_by(self, owner_id: OwnerId) -> None:
        """Guard that ``owner_id`` owns this conversation, else raise ``NotConversationOwner``."""
        if self.owner_id != owner_id:
            raise NotConversationOwner()

    @property
    def is_deleted(self) -> bool:
        """True once the conversation has been soft-deleted."""
        return self.deleted_at is not None

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Conversation) and other.id == self.id

    def __hash__(self) -> int:
        return hash(self.id)
