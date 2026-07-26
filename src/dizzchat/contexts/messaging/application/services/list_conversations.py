"""List-conversations use case."""

from __future__ import annotations

from dizzchat.contexts.messaging.domain.conversation import (
    Conversation,
    ConversationRepository,
    ParticipantId,
)


class ListConversations:
    """Return the active conversations a user takes part in, newest first.

    Includes the ones they own (an owner is always a participant) and the ones they were invited to.
    """

    def __init__(self, conversations: ConversationRepository) -> None:
        self._conversations = conversations

    async def execute(self, *, participant_id: ParticipantId) -> list[Conversation]:
        return await self._conversations.list_for_participant(participant_id)
