"""Value objects owned by the User aggregate.

Each is immutable, validated once at construction, and equal by value (``frozen``/``eq``); an
invalid value raises a domain error rather than being constructed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from uuid import UUID

from dizzchat.contexts.identity.domain.user.errors import InvalidEmail

# Pragmatic address check for this project: a single '@', a dotted domain, no whitespace.
_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@dataclass(frozen=True, eq=True, slots=True)
class Email:
    """A syntactically valid email address, normalized to lowercase."""

    value: str

    def __post_init__(self) -> None:
        normalized = self.value.strip().lower()
        if not _EMAIL_PATTERN.match(normalized):
            raise InvalidEmail(self.value)
        object.__setattr__(self, "value", normalized)

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, eq=True, slots=True)
class PasswordHash:
    """An opaque password hash.

    By construction this holds a *hash* only — the plaintext password never enters the domain,
    so it can never be persisted by accident.
    """

    value: str

    def __post_init__(self) -> None:
        if not self.value:
            raise ValueError("password hash must not be empty")

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, eq=True, slots=True)
class UserId:
    """A user's stable identity, as a value object wrapping a UUID."""

    value: UUID

    def __str__(self) -> str:
        return str(self.value)
