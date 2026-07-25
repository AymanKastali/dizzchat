"""SQLAlchemy adapter implementing the domain ``ConversationRepository`` port."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from dizzchat.contexts.messaging.domain.conversation import (
    Conversation,
    ConversationId,
    ConversationTitle,
    OwnerId,
)
from dizzchat.contexts.messaging.infrastructure.outbound.persistence.models import (
    ConversationModel,
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
    ) -> None:
        self._session.add(
            ConversationModel(
                id=conversation_id.value,
                owner_id=owner_id.value,
                title=title.value,
                created_at=created_at,
                updated_at=updated_at,
                deleted_at=None,
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

    async def list_for_owner(self, owner_id: OwnerId) -> list[Conversation]:
        result = await self._session.execute(
            select(ConversationModel)
            .where(
                ConversationModel.owner_id == owner_id.value,
                ConversationModel.deleted_at.is_(None),
            )
            .order_by(ConversationModel.created_at.desc())
        )
        return [_to_domain(model) for model in result.scalars().all()]


def _to_domain(model: ConversationModel) -> Conversation:
    return Conversation(
        id=ConversationId(model.id),
        owner_id=OwnerId(model.owner_id),
        title=ConversationTitle(model.title),
        created_at=model.created_at,
        updated_at=model.updated_at,
        deleted_at=model.deleted_at,
    )
