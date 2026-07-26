"""SQLAlchemy adapter implementing the domain ``ConversationRepository`` port."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from dizzchat.contexts.messaging.domain.conversation import (
    Conversation,
    ConversationId,
    ConversationTitle,
    OwnerId,
    Participant,
    ParticipantId,
)
from dizzchat.contexts.messaging.infrastructure.outbound.persistence.models import (
    ConversationModel,
    ConversationParticipantModel,
)


class SqlAlchemyConversationRepository:
    """Persists the ``Conversation`` aggregate, translating between domain and row model."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        conversation_id: ConversationId,
        owner_id: OwnerId,
        title: ConversationTitle,
        created_at: datetime,
        updated_at: datetime,
        participant_ids: frozenset[ParticipantId],
    ) -> None:
        self._session.add(
            ConversationModel(
                id=conversation_id.value,
                owner_id=owner_id.value,
                title=title.value,
                created_at=created_at,
                updated_at=updated_at,
                deleted_at=None,
                participants=[
                    ConversationParticipantModel(
                        conversation_id=conversation_id.value,
                        user_id=participant_id.value,
                        joined_at=created_at,
                    )
                    for participant_id in participant_ids
                ],
            )
        )

    async def update(
        self,
        *,
        conversation_id: ConversationId,
        title: ConversationTitle,
        updated_at: datetime,
        deleted_at: datetime | None,
    ) -> None:
        await self._session.execute(
            update(ConversationModel)
            .where(ConversationModel.id == conversation_id.value)
            .values(title=title.value, updated_at=updated_at, deleted_at=deleted_at)
        )

    async def get(self, conversation_id: ConversationId) -> Conversation | None:
        result = await self._session.execute(
            select(ConversationModel).where(
                ConversationModel.id == conversation_id.value,
                ConversationModel.deleted_at.is_(None),
            )
        )
        model = result.scalar_one_or_none()
        return _to_domain(model) if model is not None else None

    async def list_for_participant(self, participant_id: ParticipantId) -> list[Conversation]:
        result = await self._session.execute(
            select(ConversationModel)
            .join(
                ConversationParticipantModel,
                ConversationParticipantModel.conversation_id == ConversationModel.id,
            )
            .where(
                ConversationParticipantModel.user_id == participant_id.value,
                ConversationModel.deleted_at.is_(None),
            )
            .order_by(ConversationModel.created_at.desc())
        )
        return [_to_domain(model) for model in result.scalars().all()]

    async def add_participant(
        self,
        *,
        conversation_id: ConversationId,
        participant_id: ParticipantId,
        joined_at: datetime,
    ) -> None:
        self._session.add(
            ConversationParticipantModel(
                conversation_id=conversation_id.value,
                user_id=participant_id.value,
                joined_at=joined_at,
            )
        )

    async def remove_participant(
        self, *, conversation_id: ConversationId, participant_id: ParticipantId
    ) -> None:
        await self._session.execute(
            delete(ConversationParticipantModel).where(
                ConversationParticipantModel.conversation_id == conversation_id.value,
                ConversationParticipantModel.user_id == participant_id.value,
            )
        )

    async def list_participants(self, conversation_id: ConversationId) -> list[Participant]:
        result = await self._session.execute(
            select(ConversationParticipantModel)
            .where(ConversationParticipantModel.conversation_id == conversation_id.value)
            .order_by(ConversationParticipantModel.joined_at)
        )
        return [
            Participant(id=ParticipantId(model.user_id), joined_at=model.joined_at)
            for model in result.scalars().all()
        ]


def _to_domain(model: ConversationModel) -> Conversation:
    return Conversation(
        id=ConversationId(model.id),
        owner_id=OwnerId(model.owner_id),
        title=ConversationTitle(model.title),
        created_at=model.created_at,
        updated_at=model.updated_at,
        deleted_at=model.deleted_at,
        participant_ids=frozenset(
            ParticipantId(participant.user_id) for participant in model.participants
        ),
    )
