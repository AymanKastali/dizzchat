"""Errors raised by the User aggregate."""

from __future__ import annotations

from dizzchat.contexts.identity.domain.errors import IdentityError


class InvalidEmail(IdentityError):
    """Raised when a string cannot be a valid email address."""

    def __init__(self, value: str) -> None:
        super().__init__(f"invalid email address: {value!r}")


class EmailAlreadyRegistered(IdentityError):
    """Raised when signing up with an email that already exists."""

    def __init__(self, email: str) -> None:
        super().__init__(f"email already registered: {email}")


class InvalidCredentials(IdentityError):
    """Raised when email/password authentication fails.

    Deliberately generic — it does not reveal whether the email exists, to avoid user
    enumeration.
    """

    def __init__(self) -> None:
        super().__init__("invalid credentials")
