"""List-participants use case — who is in this conversation."""

from __future__ import annotations

from dizzchat.contexts.messaging.domain.conversation import (
    ConversationId,
    ConversationNotFound,
    ConversationRepository,
    Participant,
    ParticipantId,
)


class ListParticipants:
    """Return a conversation's memberships, readable by any of its participants."""

    def __init__(self, conversations: ConversationRepository) -> None:
        self._conversations = conversations

    async def execute(
        self, *, conversation_id: ConversationId, participant_id: ParticipantId
    ) -> list[Participant]:
        conversation = await self._conversations.get(conversation_id)
        if conversation is None:
            raise ConversationNotFound(conversation_id)
        conversation.ensure_participant(participant_id)
        return await self._conversations.list_participants(conversation_id)
