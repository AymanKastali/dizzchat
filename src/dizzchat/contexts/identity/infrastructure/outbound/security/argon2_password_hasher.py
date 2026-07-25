"""Argon2 implementation of the domain ``PasswordHasher`` port."""

from __future__ import annotations

from argon2 import PasswordHasher as Argon2Backend
from argon2.exceptions import Argon2Error, VerifyMismatchError

from dizzchat.contexts.identity.domain.user import PasswordHash


class Argon2PasswordHasher:
    """Hashes and verifies passwords with argon2id (CPU-bound; call off the event loop)."""

    def __init__(self) -> None:
        self._backend = Argon2Backend()

    def hash(self, plaintext_password: str) -> PasswordHash:
        return PasswordHash(self._backend.hash(plaintext_password))

    def verify(self, candidate: str, password_hash: PasswordHash) -> bool:
        try:
            return self._backend.verify(password_hash.value, candidate)
        except (VerifyMismatchError, Argon2Error):
            return False
