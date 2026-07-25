"""Tests for the AuthenticateUser (login) use case."""

from datetime import UTC, datetime, timedelta

import pytest

from dizzchat.contexts.identity.application.services import AuthenticateUser, RegisterUser
from dizzchat.contexts.identity.domain.user import InvalidCredentials
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
_EMAIL = "user@example.com"
_PASSWORD = PLAINTEXT_PW


async def _with_seeded_user() -> tuple[AuthenticateUser, FakeRefreshTokenRepository]:
    users = FakeUserRepository()
    refresh_tokens = FakeRefreshTokenRepository()
    hasher = FakeHasher()
    clock = FixedClock(_NOW)
    await RegisterUser(users, hasher, clock).execute(email=_EMAIL, password=_PASSWORD)
    handler = AuthenticateUser(users, refresh_tokens, hasher, FakeTokenService(), clock, _TTL)
    return handler, refresh_tokens


async def test_login_issues_a_token_pair_and_persists_the_refresh_token() -> None:
    handler, refresh_tokens = await _with_seeded_user()

    pair = await handler.execute(email=_EMAIL, password=_PASSWORD)

    assert pair.access_token.startswith("access:")
    jti, _, _ = pair.refresh_token.partition(".")
    assert await refresh_tokens.get_by_jti(jti) is not None


async def test_login_rejects_a_wrong_password() -> None:
    handler, _ = await _with_seeded_user()

    with pytest.raises(InvalidCredentials):
        await handler.execute(email=_EMAIL, password="wrong")


async def test_login_rejects_an_unknown_email() -> None:
    handler, _ = await _with_seeded_user()

    with pytest.raises(InvalidCredentials):
        await handler.execute(email="ghost@example.com", password=_PASSWORD)
