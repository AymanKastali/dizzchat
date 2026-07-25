"""List-conversations controller."""

from __future__ import annotations

from dizzchat.contexts.conversations.domain.conversation import OwnerId
from dizzchat.contexts.conversations.infrastructure.inbound.api.dependencies import (
    ListConversationsDep,
)
from dizzchat.contexts.conversations.infrastructure.inbound.api.schemas import ConversationResponse
from dizzchat.contexts.identity.infrastructure.inbound.api.dependencies import CurrentUser


async def list_conversations(
    caller: CurrentUser, service: ListConversationsDep
) -> list[ConversationResponse]:
    conversations = await service.execute(owner_id=OwnerId(caller.user_id.value))
    return [ConversationResponse.from_domain(conversation) for conversation in conversations]
