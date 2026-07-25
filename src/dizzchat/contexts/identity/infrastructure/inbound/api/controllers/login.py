"""Login controller."""

from __future__ import annotations

from dizzchat.contexts.identity.infrastructure.inbound.api.dependencies import AuthenticateUserDep
from dizzchat.contexts.identity.infrastructure.inbound.api.schemas import (
    LoginRequest,
    TokenResponse,
)


async def login(body: LoginRequest, authenticate_user: AuthenticateUserDep) -> TokenResponse:
    pair = await authenticate_user.execute(email=body.email, password=body.password)
    return TokenResponse(access_token=pair.access_token, refresh_token=pair.refresh_token)
