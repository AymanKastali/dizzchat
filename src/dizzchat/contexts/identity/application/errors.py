"""Application-layer errors for the Identity context."""

from __future__ import annotations

from dizzchat.contexts.identity.domain.errors import IdentityError


class InvalidAccessToken(IdentityError):
    """Raised when an access token is missing, malformed, or expired."""

    def __init__(self) -> None:
        super().__init__("invalid or expired access token")
