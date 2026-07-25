"""Technical ports the Identity use cases depend on.

Unlike the repository ports (which belong to the domain), these abstract infrastructure
capabilities — token minting/validation and the wall clock. Concrete adapters live in
``infrastructure/outbound``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from dizzchat.contexts.identity.application.dto import AccessClaims, GeneratedRefreshToken
from dizzchat.contexts.identity.domain.user import UserId


class TokenService(Protocol):
    """Issues and validates short-lived access tokens, and mints/parses opaque refresh tokens."""

    def issue_access(self, user_id: UserId) -> str:
        """Return a signed, short-lived access token for ``user_id``."""
        ...

    def decode_access(self, token: str) -> AccessClaims:
        """Validate an access token and return its claims, or raise ``InvalidAccessToken``."""
        ...

    def generate_refresh(self) -> GeneratedRefreshToken:
        """Mint a new refresh token (a random secret plus the hash to persist)."""
        ...

    def parse_refresh(self, token: str) -> tuple[str, str]:
        """Split a client refresh token into ``(jti, secret)``, or raise ``InvalidRefreshToken``."""
        ...

    def verify_refresh(self, secret: str, token_hash: str) -> bool:
        """Return whether ``secret`` matches ``token_hash`` (constant-time)."""
        ...


class Clock(Protocol):
    """A source of the current time, injected so time-dependent logic stays testable."""

    def now(self) -> datetime:
        """Return the current timezone-aware UTC time."""
        ...
