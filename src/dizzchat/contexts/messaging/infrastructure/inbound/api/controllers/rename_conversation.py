"""Rename-conversation controller."""

from __future__ import annotations

from uuid import UUID

from dizzchat.contexts.identity.infrastructure.inbound.api.dependencies import CurrentUser
from dizzchat.contexts.messaging.domain.conversation import ConversationId, OwnerId
from dizzchat.contexts.messaging.infrastructure.inbound.api.dependencies import (
    RenameConversationDep,
)
from dizzchat.contexts.messaging.infrastructure.inbound.api.schemas import (
    ConversationResponse,
    RenameConversationRequest,
)


async def rename_conversation(
    conversation_id: UUID,
    body: RenameConversationRequest,
    caller: CurrentUser,
    service: RenameConversationDep,
) -> ConversationResponse:
    conversation = await service.execute(
        conversation_id=ConversationId(conversation_id),
        owner_id=OwnerId(caller.user_id.value),
        new_title=body.title,
    )
    return ConversationResponse.from_domain(conversation)
