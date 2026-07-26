"""SQLAlchemy row model for the ``conversations`` table."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from dizzchat.contexts.messaging.infrastructure.outbound.persistence.models.conversation_participant_model import (  # noqa: E501
    ConversationParticipantModel,
)
from dizzchat.shared.infrastructure.outbound.database import Base


class ConversationModel(Base):
    __tablename__ = "conversations"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    owner_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    title: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # ``selectin`` is required, not stylistic: the default lazy loader emits I/O on attribute
    # access, which raises under asyncio. It also batches, so listing a user's conversations
    # loads every participant set in one extra query rather than one per row.
    participants: Mapped[list[ConversationParticipantModel]] = relationship(
        lazy="selectin", cascade="all, delete-orphan"
    )
