"""Repository port for the RefreshToken aggregate root.

Declared in the domain; the concrete adapter lives in ``infrastructure/outbound/persistence``.
"""

from __future__ import annotations

from typing import Protocol

from dizzchat.contexts.identity.domain.refresh_token.refresh_token import RefreshToken


class RefreshTokenRepository(Protocol):
    """Collection-like persistence for the ``RefreshToken`` aggregate root."""

    async def add(self, token: RefreshToken) -> None:
        """Persist a newly issued refresh token."""
        ...

    async def get_by_jti(self, jti: str) -> RefreshToken | None:
        """Return the token with this ``jti``, or ``None`` if there is none."""
        ...

    async def save(self, token: RefreshToken) -> None:
        """Persist changes to an existing token (e.g. after revocation)."""
        ...
