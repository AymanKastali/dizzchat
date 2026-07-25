"""Password hashing port used by the User aggregate.

Declared in the domain because ``User`` depends on it directly to enforce its password rules
(double dispatch); the concrete argon2 adapter lives in ``infrastructure/outbound/security``.
"""

from __future__ import annotations

from typing import Protocol

from dizzchat.contexts.identity.domain.user.value_objects import PasswordHash


class PasswordHasher(Protocol):
    """Hashes a plaintext password and verifies a candidate against a stored hash."""

    def hash(self, plaintext_password: str) -> PasswordHash:
        """Return the hash of ``plaintext_password``."""
        ...

    def verify(self, candidate: str, password_hash: PasswordHash) -> bool:
        """Return whether ``candidate`` matches ``password_hash``."""
        ...
