"""Restore-conversation controller (undo a soft-delete)."""

from __future__ import annotations

from uuid import UUID

from dizzchat.contexts.identity.infrastructure.inbound.api.dependencies import CurrentUser
from dizzchat.contexts.messaging.domain.conversation import ConversationId, OwnerId
from dizzchat.contexts.messaging.infrastructure.inbound.api.dependencies import (
    RestoreConversationDep,
)
from dizzchat.contexts.messaging.infrastructure.inbound.api.schemas import ConversationResponse


async def restore_conversation(
    conversation_id: UUID, caller: CurrentUser, service: RestoreConversationDep
) -> ConversationResponse:
    conversation = await service.execute(
        conversation_id=ConversationId(conversation_id), owner_id=OwnerId(caller.user_id.value)
    )
    return ConversationResponse.from_domain(conversation)
