"""List-conversations controller."""

from __future__ import annotations

from dizzchat.contexts.identity.infrastructure.inbound.api.dependencies import CurrentUser
from dizzchat.contexts.messaging.domain.conversation import ParticipantId
from dizzchat.contexts.messaging.infrastructure.inbound.api.dependencies import (
    ListConversationsDep,
)
from dizzchat.contexts.messaging.infrastructure.inbound.api.schemas import ConversationResponse


async def list_conversations(
    caller: CurrentUser, service: ListConversationsDep
) -> list[ConversationResponse]:
    conversations = await service.execute(participant_id=ParticipantId(caller.user_id.value))
    return [ConversationResponse.from_domain(conversation) for conversation in conversations]
