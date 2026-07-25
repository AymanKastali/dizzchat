"""SQLAlchemy adapter implementing the domain ``MessageRepository`` port."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dizzchat.contexts.messaging.domain.conversation import ConversationId
from dizzchat.contexts.messaging.domain.message import (
    ClientMessageId,
    Message,
    MessageContent,
    MessageId,
    MessageRole,
    SenderId,
)
from dizzchat.contexts.messaging.infrastructure.outbound.persistence.models import MessageModel


class SqlAlchemyMessageRepository:
    """Persists the ``Message`` aggregate, translating between domain and row model."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        conversation_id: ConversationId,
        sender_id: SenderId | None,
        role: MessageRole,
        content: MessageContent,
        created_at: datetime,
        client_message_id: ClientMessageId | None = None,
    ) -> Message:
        model = MessageModel(
            conversation_id=conversation_id.value,
            sender_id=sender_id.value if sender_id is not None else None,
            role=role.value,
            content=content.value,
            created_at=created_at,
            client_message_id=client_message_id.value if client_message_id is not None else None,
        )
        self._session.add(model)
        # Flush so the database assigns the bigserial id before we hand the message back.
        await self._session.flush()
        return _to_domain(model)

    async def find_by_client_message_id(
        self, conversation_id: ConversationId, client_message_id: ClientMessageId
    ) -> Message | None:
        stmt = select(MessageModel).where(
            MessageModel.conversation_id == conversation_id.value,
            MessageModel.client_message_id == client_message_id.value,
        )
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return _to_domain(model) if model is not None else None

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

    async def list_since(
        self,
        conversation_id: ConversationId,
        *,
        after: MessageId | None,
        limit: int,
    ) -> list[Message]:
        stmt = select(MessageModel).where(MessageModel.conversation_id == conversation_id.value)
        if after is not None:
            stmt = stmt.where(MessageModel.id > after.value)
        stmt = stmt.order_by(MessageModel.id.asc()).limit(limit)
        result = await self._session.execute(stmt)
        return [_to_domain(model) for model in result.scalars().all()]


def _to_domain(model: MessageModel) -> Message:
    return Message(
        id=MessageId(model.id),
        conversation_id=ConversationId(model.conversation_id),
        sender_id=SenderId(model.sender_id) if model.sender_id is not None else None,
        role=MessageRole(model.role),
        content=MessageContent(model.content),
        created_at=model.created_at,
        client_message_id=ClientMessageId(model.client_message_id)
        if model.client_message_id is not None
        else None,
    )
