"""Signup controller."""

from __future__ import annotations

from dizzchat.contexts.identity.infrastructure.inbound.api.dependencies import RegisterUserDep
from dizzchat.contexts.identity.infrastructure.inbound.api.schemas import (
    SignupRequest,
    UserResponse,
)


async def signup(body: SignupRequest, register_user: RegisterUserDep) -> UserResponse:
    user = await register_user.execute(email=body.email, password=body.password)
    return UserResponse(id=user.id.value, email=user.email.value, created_at=user.created_at)
