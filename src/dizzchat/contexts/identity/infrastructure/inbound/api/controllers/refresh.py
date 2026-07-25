"""Refresh controller."""

from __future__ import annotations

from dizzchat.contexts.identity.infrastructure.inbound.api.dependencies import RefreshAccessTokenDep
from dizzchat.contexts.identity.infrastructure.inbound.api.schemas import (
    RefreshRequest,
    TokenResponse,
)


async def refresh(
    body: RefreshRequest, refresh_access_token: RefreshAccessTokenDep
) -> TokenResponse:
    pair = await refresh_access_token.execute(refresh_token=body.refresh_token)
    return TokenResponse(access_token=pair.access_token, refresh_token=pair.refresh_token)
