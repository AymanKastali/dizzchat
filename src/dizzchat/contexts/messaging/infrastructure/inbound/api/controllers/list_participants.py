"""List-participants controller."""

from __future__ import annotations

from uuid import UUID

from dizzchat.contexts.identity.infrastructure.inbound.api.dependencies import CurrentUser
from dizzchat.contexts.messaging.domain.conversation import ConversationId, ParticipantId
from dizzchat.contexts.messaging.infrastructure.inbound.api.dependencies import ListParticipantsDep
from dizzchat.contexts.messaging.infrastructure.inbound.api.schemas import ParticipantResponse


async def list_participants(
    conversation_id: UUID, caller: CurrentUser, service: ListParticipantsDep
) -> list[ParticipantResponse]:
    participants = await service.execute(
        conversation_id=ConversationId(conversation_id),
        participant_id=ParticipantId(caller.user_id.value),
    )
    return [ParticipantResponse.from_domain(participant) for participant in participants]
