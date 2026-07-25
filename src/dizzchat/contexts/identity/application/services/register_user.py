"""Register-user use case."""

from __future__ import annotations

import asyncio
from uuid import uuid4

from dizzchat.contexts.identity.application.ports import Clock
from dizzchat.contexts.identity.domain.user import (
    Email,
    EmailAlreadyRegistered,
    PasswordHasher,
    User,
    UserId,
    UserRepository,
)


class RegisterUser:
    """Register a new user, rejecting a duplicate email."""

    def __init__(self, users: UserRepository, hasher: PasswordHasher, clock: Clock) -> None:
        self._users = users
        self._hasher = hasher
        self._clock = clock

    async def execute(self, *, email: str, password: str) -> User:
        address = Email(email)
        if await self._users.get_by_email(address) is not None:
            raise EmailAlreadyRegistered(address.value)
        # Hashing is CPU-bound; offload it so it never blocks the event loop.
        user = await asyncio.to_thread(
            User.register,
            user_id=UserId(uuid4()),
            email=address,
            plaintext_password=password,
            hasher=self._hasher,
            created_at=self._clock.now(),
        )
        await self._users.add(user)
        return user
