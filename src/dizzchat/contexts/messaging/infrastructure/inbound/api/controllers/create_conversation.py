"""Create-conversation controller."""

from __future__ import annotations

from dizzchat.contexts.identity.infrastructure.inbound.api.dependencies import CurrentUser
from dizzchat.contexts.messaging.domain.conversation import OwnerId
from dizzchat.contexts.messaging.infrastructure.inbound.api.dependencies import (
    CreateConversationDep,
)
from dizzchat.contexts.messaging.infrastructure.inbound.api.schemas import (
    ConversationResponse,
    CreateConversationRequest,
)


async def create_conversation(
    body: CreateConversationRequest, caller: CurrentUser, service: CreateConversationDep
) -> ConversationResponse:
    conversation = await service.execute(owner_id=OwnerId(caller.user_id.value), title=body.title)
    return ConversationResponse.from_domain(conversation)
