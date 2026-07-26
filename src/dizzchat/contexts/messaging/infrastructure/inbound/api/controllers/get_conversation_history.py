"""Get-conversation-history controller (cursor-paginated)."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import Query

from dizzchat.contexts.identity.infrastructure.inbound.api.dependencies import CurrentUser
from dizzchat.contexts.messaging.domain.conversation import ConversationId, ParticipantId
from dizzchat.contexts.messaging.domain.message import MessageId
from dizzchat.contexts.messaging.infrastructure.inbound.api.dependencies import (
    GetConversationHistoryDep,
)
from dizzchat.contexts.messaging.infrastructure.inbound.api.schemas import MessagePageResponse


async def get_conversation_history(
    conversation_id: UUID,
    caller: CurrentUser,
    service: GetConversationHistoryDep,
    before: Annotated[int | None, Query(ge=1)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> MessagePageResponse:
    page = await service.execute(
        conversation_id=ConversationId(conversation_id),
        participant_id=ParticipantId(caller.user_id.value),
        before=MessageId(before) if before is not None else None,
        limit=limit,
    )
    return MessagePageResponse.from_domain(page)
