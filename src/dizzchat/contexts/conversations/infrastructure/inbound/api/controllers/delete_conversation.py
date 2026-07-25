"""Delete-conversation controller (soft-delete)."""

from __future__ import annotations

from uuid import UUID

from dizzchat.contexts.conversations.domain.conversation import ConversationId, OwnerId
from dizzchat.contexts.conversations.infrastructure.inbound.api.dependencies import (
    DeleteConversationDep,
)
from dizzchat.contexts.identity.infrastructure.inbound.api.dependencies import CurrentUser


async def delete_conversation(
    conversation_id: UUID, caller: CurrentUser, service: DeleteConversationDep
) -> None:
    await service.execute(
        conversation_id=ConversationId(conversation_id), owner_id=OwnerId(caller.user_id.value)
    )
