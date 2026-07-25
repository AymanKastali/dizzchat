"""Authenticate-user (login) use case."""

from __future__ import annotations

import asyncio
from datetime import timedelta

from dizzchat.contexts.identity.application.dto import TokenPair
from dizzchat.contexts.identity.application.ports import Clock, TokenService
from dizzchat.contexts.identity.domain.refresh_token import RefreshToken, RefreshTokenRepository
from dizzchat.contexts.identity.domain.user import (
    Email,
    InvalidCredentials,
    InvalidEmail,
    PasswordHasher,
    UserRepository,
)


class AuthenticateUser:
    """Verify credentials and issue an access + refresh token pair."""

    def __init__(
        self,
        users: UserRepository,
        refresh_tokens: RefreshTokenRepository,
        hasher: PasswordHasher,
        tokens: TokenService,
        clock: Clock,
        refresh_ttl: timedelta,
    ) -> None:
        self._users = users
        self._refresh_tokens = refresh_tokens
        self._hasher = hasher
        self._tokens = tokens
        self._clock = clock
        self._refresh_ttl = refresh_ttl

    async def execute(self, *, email: str, password: str) -> TokenPair:
        try:
            address = Email(email)
        except InvalidEmail:
            # A malformed email cannot match any user; stay generic to avoid enumeration.
            raise InvalidCredentials() from None
        user = await self._users.get_by_email(address)
        if user is None:
            # Do equivalent hashing work for an unknown email so response latency matches the
            # found-user path — otherwise timing alone reveals which emails are registered.
            await asyncio.to_thread(self._hasher.hash, password)
            raise InvalidCredentials()
        # Verifying is CPU-bound; offload it so it never blocks the event loop.
        if not await asyncio.to_thread(user.verify_password, password, self._hasher):
            raise InvalidCredentials()

        generated = self._tokens.generate_refresh()
        refresh = RefreshToken.issue(
            jti=generated.jti,
            user_id=user.id,
            token_hash=generated.token_hash,
            issued_at=self._clock.now(),
            ttl=self._refresh_ttl,
        )
        await self._refresh_tokens.add(refresh)
        return TokenPair(
            access_token=self._tokens.issue_access(user.id),
            refresh_token=generated.token,
        )
