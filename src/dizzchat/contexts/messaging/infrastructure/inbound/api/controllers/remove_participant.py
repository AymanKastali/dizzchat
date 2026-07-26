"""Remove-participant controller (the owner removes someone, or a participant leaves)."""

from __future__ import annotations

from uuid import UUID

from dizzchat.contexts.identity.infrastructure.inbound.api.dependencies import CurrentUser
from dizzchat.contexts.messaging.domain.conversation import ConversationId, ParticipantId
from dizzchat.contexts.messaging.infrastructure.inbound.api.dependencies import RemoveParticipantDep


async def remove_participant(
    conversation_id: UUID, user_id: UUID, caller: CurrentUser, service: RemoveParticipantDep
) -> None:
    await service.execute(
        conversation_id=ConversationId(conversation_id),
        actor_id=ParticipantId(caller.user_id.value),
        participant_id=ParticipantId(user_id),
    )
