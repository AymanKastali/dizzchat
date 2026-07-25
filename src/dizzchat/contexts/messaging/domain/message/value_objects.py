"""Value objects owned by the Message aggregate.

Each is immutable, validated once at construction, and equal by value (``frozen``/``eq``); an
invalid value raises a domain error rather than being constructed.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum, auto
from uuid import UUID

from dizzchat.contexts.messaging.domain.message.errors import InvalidMessageContent


class MessageRole(StrEnum):
    """Who authored a message: a human ``USER`` or the ``ASSISTANT``."""

    USER = auto()
    ASSISTANT = auto()


@dataclass(frozen=True, eq=True, slots=True)
class MessageId:
    """A message's identity and ordering key — the persisted ``bigserial`` sequence number."""

    value: int

    def __str__(self) -> str:
        return str(self.value)


@dataclass(frozen=True, eq=True, slots=True)
class SenderId:
    """A reference to the Identity user who sent a message, by id."""

    value: UUID

    def __str__(self) -> str:
        return str(self.value)


@dataclass(frozen=True, eq=True, slots=True)
class ClientMessageId:
    """A client-generated idempotency key for a send, unique per conversation."""

    value: UUID

    def __str__(self) -> str:
        return str(self.value)


@dataclass(frozen=True, eq=True, slots=True)
class MessageContent:
    """A message body, required to be non-empty."""

    value: str

    def __post_init__(self) -> None:
        if not self.value.strip():
            raise InvalidMessageContent()

    def __str__(self) -> str:
        return self.value
