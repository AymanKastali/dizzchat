"""Tests for the RegisterUser use case."""

from datetime import UTC, datetime

import pytest

from dizzchat.contexts.identity.application.services import RegisterUser
from dizzchat.contexts.identity.domain.user import EmailAlreadyRegistered, InvalidEmail
from tests.contexts.identity.credentials import PLAINTEXT_PW
from tests.contexts.identity.fakes import FakeHasher, FakeUserRepository, FixedClock

_NOW = datetime(2024, 1, 1, tzinfo=UTC)


def _handler(users: FakeUserRepository) -> RegisterUser:
    return RegisterUser(users, FakeHasher(), FixedClock(_NOW))


async def test_registers_a_new_user_with_a_normalized_email_and_hashed_password() -> None:
    users = FakeUserRepository()

    user = await _handler(users).execute(email="User@Example.com", password=PLAINTEXT_PW)

    assert user.email.value == "user@example.com"
    assert user.password_hash.value == f"hashed:{PLAINTEXT_PW}"
    assert await users.get_by_email(user.email) is user


async def test_rejects_a_duplicate_email() -> None:
    users = FakeUserRepository()
    handler = _handler(users)
    await handler.execute(email="user@example.com", password=PLAINTEXT_PW)

    with pytest.raises(EmailAlreadyRegistered):
        await handler.execute(email="user@example.com", password=PLAINTEXT_PW + "-2")


async def test_rejects_an_invalid_email() -> None:
    with pytest.raises(InvalidEmail):
        await _handler(FakeUserRepository()).execute(email="not-an-email", password=PLAINTEXT_PW)
