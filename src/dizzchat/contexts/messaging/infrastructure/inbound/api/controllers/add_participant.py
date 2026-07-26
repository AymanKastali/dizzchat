"""Add-participant controller (owner invites a registered user by email)."""

from __future__ import annotations

from uuid import UUID

from dizzchat.contexts.identity.infrastructure.inbound.api.dependencies import CurrentUser
from dizzchat.contexts.messaging.domain.conversation import ConversationId, OwnerId
from dizzchat.contexts.messaging.infrastructure.inbound.api.dependencies import AddParticipantDep
from dizzchat.contexts.messaging.infrastructure.inbound.api.schemas import AddParticipantRequest


async def add_participant(
    conversation_id: UUID,
    body: AddParticipantRequest,
    caller: CurrentUser,
    service: AddParticipantDep,
) -> None:
    await service.execute(
        conversation_id=ConversationId(conversation_id),
        owner_id=OwnerId(caller.user_id.value),
        email=body.email,
    )
