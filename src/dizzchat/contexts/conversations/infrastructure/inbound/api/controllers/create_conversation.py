"""Create-conversation controller."""

from __future__ import annotations

from dizzchat.contexts.conversations.domain.conversation import OwnerId
from dizzchat.contexts.conversations.infrastructure.inbound.api.dependencies import (
    CreateConversationDep,
)
from dizzchat.contexts.conversations.infrastructure.inbound.api.schemas import (
    ConversationResponse,
    CreateConversationRequest,
)
from dizzchat.contexts.identity.infrastructure.inbound.api.dependencies import CurrentUser


async def create_conversation(
    body: CreateConversationRequest, caller: CurrentUser, service: CreateConversationDep
) -> ConversationResponse:
    conversation = await service.execute(owner_id=OwnerId(caller.user_id.value), title=body.title)
    return ConversationResponse.from_domain(conversation)
