"""SQLAlchemy adapter implementing the domain ``MessageRepository`` port."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dizzchat.contexts.conversations.domain.conversation import ConversationId
from dizzchat.contexts.conversations.domain.message import (
    Message,
    MessageContent,
    MessageId,
    SenderId,
)
from dizzchat.contexts.conversations.infrastructure.outbound.persistence.models import MessageModel


class SqlAlchemyMessageRepository:
    """Persists the ``Message`` aggregate, translating between domain and row model."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        conversation_id: ConversationId,
        sender_id: SenderId,
        content: MessageContent,
        created_at: datetime,
    ) -> Message:
        model = MessageModel(
            conversation_id=conversation_id.value,
            sender_id=sender_id.value,
            content=content.value,
            created_at=created_at,
        )
        self._session.add(model)
        # Flush so the database assigns the bigserial id before we hand the message back.
        await self._session.flush()
        return _to_domain(model)

    async def list_history(
        self,
        conversation_id: ConversationId,
        *,
        before: MessageId | None,
        limit: int,
    ) -> list[Message]:
        stmt = select(MessageModel).where(MessageModel.conversation_id == conversation_id.value)
        if before is not None:
            stmt = stmt.where(MessageModel.id < before.value)
        stmt = stmt.order_by(MessageModel.id.desc()).limit(limit)
        result = await self._session.execute(stmt)
        return [_to_domain(model) for model in result.scalars().all()]


def _to_domain(model: MessageModel) -> Message:
    return Message(
        id=MessageId(model.id),
        conversation_id=ConversationId(model.conversation_id),
        sender_id=SenderId(model.sender_id),
        content=MessageContent(model.content),
        created_at=model.created_at,
    )
