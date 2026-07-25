"""Repository port for the User aggregate root.

Declared in the domain (the aggregate owns the contract for its own persistence); the concrete
adapter lives in ``infrastructure/outbound/persistence``.
"""

from __future__ import annotations

from typing import Protocol

from dizzchat.contexts.identity.domain.user.user import User
from dizzchat.contexts.identity.domain.user.value_objects import Email


class UserRepository(Protocol):
    """Collection-like persistence for the ``User`` aggregate root."""

    async def add(self, user: User) -> None:
        """Persist a newly registered user."""
        ...

    async def get_by_email(self, email: Email) -> User | None:
        """Return the user with this email, or ``None`` if there is none."""
        ...
