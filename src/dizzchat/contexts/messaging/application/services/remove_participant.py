"""Remove-participant use case — the owner removes someone, or a participant leaves."""

from __future__ import annotations

from dizzchat.contexts.messaging.domain.conversation import (
    ConversationId,
    ConversationNotFound,
    ConversationRepository,
    NotConversationOwner,
    OwnerId,
    ParticipantId,
)


class RemoveParticipant:
    """Drop a membership.

    One use case covers both directions, because the rule is the same shape: the owner may remove
    anyone, and anyone may remove themselves (leaving). The owner cannot be removed at all — the
    aggregate refuses that, so a conversation is never left with an owner who cannot read it.
    """

    def __init__(self, conversations: ConversationRepository) -> None:
        self._conversations = conversations

    async def execute(
        self,
        *,
        conversation_id: ConversationId,
        actor_id: ParticipantId,
        participant_id: ParticipantId,
    ) -> None:
        conversation = await self._conversations.get(conversation_id)
        if conversation is None:
            raise ConversationNotFound(conversation_id)

        is_owner = conversation.owner_id == OwnerId(actor_id.value)
        if not is_owner and actor_id != participant_id:
            raise NotConversationOwner()

        conversation.remove_participant(participant_id)
        await self._conversations.remove_participant(
            conversation_id=conversation_id, participant_id=participant_id
        )
