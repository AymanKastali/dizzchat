"""Add-participant use case — the owner admits another user to their conversation."""

from __future__ import annotations

from dizzchat.contexts.messaging.application.ports import UserDirectory
from dizzchat.contexts.messaging.domain.conversation import (
    ConversationId,
    ConversationNotFound,
    ConversationRepository,
    OwnerId,
    ParticipantId,
    ParticipantUserNotFound,
)
from dizzchat.shared.application import Clock


class AddParticipant:
    """Admit the user with this email, at the owner's request.

    Idempotent: re-inviting an existing participant succeeds and writes nothing, so a client that
    retries an invite cannot create a duplicate membership.
    """

    def __init__(
        self,
        conversations: ConversationRepository,
        users: UserDirectory,
        clock: Clock,
    ) -> None:
        self._conversations = conversations
        self._users = users
        self._clock = clock

    async def execute(
        self, *, conversation_id: ConversationId, owner_id: OwnerId, email: str
    ) -> ParticipantId:
        """Admit the invited user and return their id. Only the owner may invite."""
        conversation = await self._conversations.get(conversation_id)
        if conversation is None:
            raise ConversationNotFound(conversation_id)
        conversation.ensure_owned_by(owner_id)

        user_id = await self._users.find_id_by_email(email)
        if user_id is None:
            raise ParticipantUserNotFound(email)

        participant_id = ParticipantId(user_id)
        if conversation.add_participant(participant_id):
            await self._conversations.add_participant(
                conversation_id=conversation_id,
                participant_id=participant_id,
                joined_at=self._clock.now(),
            )
        return participant_id
