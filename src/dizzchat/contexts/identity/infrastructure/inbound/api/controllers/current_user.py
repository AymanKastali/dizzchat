"""Current-user controller."""

from __future__ import annotations

from dizzchat.contexts.identity.infrastructure.inbound.api.dependencies import CurrentUser
from dizzchat.contexts.identity.infrastructure.inbound.api.schemas import CurrentUserResponse


async def current_user(caller: CurrentUser) -> CurrentUserResponse:
    return CurrentUserResponse(user_id=caller.user_id.value)
