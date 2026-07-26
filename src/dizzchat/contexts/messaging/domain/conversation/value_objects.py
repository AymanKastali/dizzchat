"""Value objects owned by the Conversation aggregate.

Each is immutable, validated once at construction, and equal by value (``frozen``/``eq``); an
invalid value raises a domain error rather than being constructed.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from dizzchat.contexts.messaging.domain.conversation.errors import InvalidConversationTitle

# A pragmatic upper bound so the title fits a bounded column and can't be abused.
_MAX_TITLE_LENGTH = 200


@dataclass(frozen=True, eq=True, slots=True)
class ConversationId:
    """A conversation's stable identity, as a value object wrapping a UUID."""

    value: UUID

    def __str__(self) -> str:
        return str(self.value)


@dataclass(frozen=True, eq=True, slots=True)
class OwnerId:
    """A reference to the Identity user who owns a conversation.

    Kept local to this context (rather than importing Identity's ``UserId``) so the two bounded
    contexts stay decoupled — a conversation references its owner by identity only.
    """

    value: UUID

    def __str__(self) -> str:
        return str(self.value)


@dataclass(frozen=True, eq=True, slots=True)
class ParticipantId:
    """A reference to an Identity user who takes part in a conversation.

    Distinct from :class:`OwnerId` because the two name different roles: the owner administers the
    conversation (rename, delete, invite), while participants may read and post. The owner is
    always among the participants. Like ``OwnerId``, it references the user by identity only, so
    this context stays decoupled from Identity.
    """

    value: UUID

    def __str__(self) -> str:
        return str(self.value)


@dataclass(frozen=True, eq=True, slots=True)
class ConversationTitle:
    """A conversation title, trimmed and required to be non-empty."""

    value: str

    def __post_init__(self) -> None:
        normalized = self.value.strip()
        if not normalized or len(normalized) > _MAX_TITLE_LENGTH:
            raise InvalidConversationTitle(self.value)
        object.__setattr__(self, "value", normalized)

    def __str__(self) -> str:
        return self.value
