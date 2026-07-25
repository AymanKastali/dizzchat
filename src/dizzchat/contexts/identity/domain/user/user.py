"""User aggregate root."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from dizzchat.contexts.identity.domain.user.password_hasher import PasswordHasher
from dizzchat.contexts.identity.domain.user.value_objects import Email, PasswordHash, UserId


@dataclass(eq=False, slots=True)
class User:
    """A registered user, identified by ``id``.

    Owns its password rules — created via :meth:`register` (which hashes, so plaintext never
    enters the model) and authenticated via :meth:`verify_password`. An entity: equal by identity.
    """

    id: UserId
    email: Email
    password_hash: PasswordHash
    created_at: datetime

    @classmethod
    def register(
        cls,
        *,
        user_id: UserId,
        email: Email,
        plaintext_password: str,
        hasher: PasswordHasher,
        created_at: datetime,
    ) -> User:
        """Register a new user, storing only the hash of ``plaintext_password``."""
        return cls(
            id=user_id,
            email=email,
            password_hash=hasher.hash(plaintext_password),
            created_at=created_at,
        )

    def verify_password(self, candidate: str, hasher: PasswordHasher) -> bool:
        """Return whether ``candidate`` matches this user's stored password."""
        return hasher.verify(candidate, self.password_hash)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, User) and other.id == self.id

    def __hash__(self) -> int:
        return hash(self.id)
