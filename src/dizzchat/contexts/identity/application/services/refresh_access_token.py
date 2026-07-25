"""Refresh-access-token (rotation) use case."""

from __future__ import annotations

from datetime import timedelta

from dizzchat.contexts.identity.application.dto import TokenPair
from dizzchat.contexts.identity.application.ports import Clock, TokenService
from dizzchat.contexts.identity.domain.refresh_token import (
    InvalidRefreshToken,
    RefreshTokenRepository,
)


class RefreshAccessToken:
    """Rotate a refresh token: validate it, revoke it, and issue a fresh pair."""

    def __init__(
        self,
        refresh_tokens: RefreshTokenRepository,
        tokens: TokenService,
        clock: Clock,
        refresh_ttl: timedelta,
    ) -> None:
        self._refresh_tokens = refresh_tokens
        self._tokens = tokens
        self._clock = clock
        self._refresh_ttl = refresh_ttl

    async def execute(self, *, refresh_token: str) -> TokenPair:
        jti, secret = self._tokens.parse_refresh(refresh_token)
        stored = await self._refresh_tokens.get_by_jti(jti)
        if stored is None or not self._tokens.verify_refresh(secret, stored.token_hash):
            raise InvalidRefreshToken()

        generated = self._tokens.generate_refresh()
        # rotate() enforces the invariant that an inactive (revoked/expired) token cannot be
        # rotated — which is what rejects replay of an already-used refresh token.
        rotated = stored.rotate(
            now=self._clock.now(),
            new_jti=generated.jti,
            new_token_hash=generated.token_hash,
            ttl=self._refresh_ttl,
        )
        await self._refresh_tokens.save(stored)
        await self._refresh_tokens.add(rotated)
        return TokenPair(
            access_token=self._tokens.issue_access(stored.user_id),
            refresh_token=generated.token,
        )
