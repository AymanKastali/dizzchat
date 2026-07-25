"""Tests for the RefreshAccessToken (rotation) use case."""

from datetime import UTC, datetime, timedelta

import pytest

from dizzchat.contexts.identity.application.services import (
    AuthenticateUser,
    RefreshAccessToken,
    RegisterUser,
)
from dizzchat.contexts.identity.domain.refresh_token import InvalidRefreshToken
from tests.contexts.identity.credentials import PLAINTEXT_PW
from tests.contexts.identity.fakes import (
    FakeHasher,
    FakeRefreshTokenRepository,
    FakeTokenService,
    FakeUserRepository,
    FixedClock,
)

_NOW = datetime(2024, 1, 1, tzinfo=UTC)
_TTL = timedelta(days=14)


async def _login_then_refresh_handler() -> tuple[str, RefreshAccessToken]:
    """Register + log in a user (sharing one token service / repo), returning a refresh token
    and a rotation handler wired to the same state."""
    users = FakeUserRepository()
    refresh_tokens = FakeRefreshTokenRepository()
    hasher = FakeHasher()
    clock = FixedClock(_NOW)
    tokens = FakeTokenService()
    await RegisterUser(users, hasher, clock).execute(
        email="user@example.com", password=PLAINTEXT_PW
    )
    pair = await AuthenticateUser(users, refresh_tokens, hasher, tokens, clock, _TTL).execute(
        email="user@example.com", password=PLAINTEXT_PW
    )
    handler = RefreshAccessToken(refresh_tokens, tokens, clock, _TTL)
    return pair.refresh_token, handler


async def test_refresh_rotates_the_token_and_rejects_reuse_of_the_old_one() -> None:
    old_refresh, handler = await _login_then_refresh_handler()

    rotated = await handler.execute(refresh_token=old_refresh)
    assert rotated.refresh_token != old_refresh
    assert rotated.access_token.startswith("access:")

    # The old refresh token is now revoked — replaying it must fail.
    with pytest.raises(InvalidRefreshToken):
        await handler.execute(refresh_token=old_refresh)


async def test_refresh_rejects_an_unknown_token() -> None:
    handler = RefreshAccessToken(
        FakeRefreshTokenRepository(), FakeTokenService(), FixedClock(_NOW), _TTL
    )

    with pytest.raises(InvalidRefreshToken):
        await handler.execute(refresh_token="jti-unknown.secret")


async def test_refresh_rejects_a_malformed_token() -> None:
    handler = RefreshAccessToken(
        FakeRefreshTokenRepository(), FakeTokenService(), FixedClock(_NOW), _TTL
    )

    with pytest.raises(InvalidRefreshToken):
        await handler.execute(refresh_token="garbage-without-a-dot")
