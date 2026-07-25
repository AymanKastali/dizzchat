"""SQLAlchemy row model for the ``messages`` table."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from dizzchat.shared.infrastructure.outbound.database import Base


class MessageModel(Base):
    __tablename__ = "messages"
    __table_args__ = (
        # Composite index for keyset history: WHERE conversation_id = ? [AND id < ?] ORDER BY id.
        Index("ix_messages_conversation_id_id", "conversation_id", "id"),
        # Idempotent send: at most one message per (conversation, client id). NULLs are distinct in
        # Postgres, so assistant messages and keyless sends never collide here.
        UniqueConstraint(
            "conversation_id",
            "client_message_id",
            name="uq_messages_conversation_id_client_message_id",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    conversation_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("conversations.id"))
    # Nullable: an assistant message has no Identity user as its sender.
    sender_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # Client-generated idempotency key; NULL for assistant messages and keyless sends.
    client_message_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
