"""Errors raised by the RefreshToken aggregate."""

from __future__ import annotations

from dizzchat.contexts.identity.domain.errors import IdentityError


class InvalidRefreshToken(IdentityError):
    """Raised when a refresh token is unknown, malformed, expired, or revoked."""

    def __init__(self) -> None:
        super().__init__("invalid or expired refresh token")
