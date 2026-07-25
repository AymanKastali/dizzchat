"""SQLAlchemy row model for the ``messages`` table."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from dizzchat.shared.infrastructure.outbound.database import Base


class MessageModel(Base):
    __tablename__ = "messages"
    # Composite index for keyset history: WHERE conversation_id = ? [AND id < ?] ORDER BY id DESC.
    __table_args__ = (Index("ix_messages_conversation_id_id", "conversation_id", "id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    conversation_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("conversations.id"))
    sender_id: Mapped[UUID] = mapped_column(Uuid)
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
