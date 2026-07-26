"""SQLAlchemy row model for the ``conversation_participants`` table."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from dizzchat.shared.infrastructure.outbound.database import Base


class ConversationParticipantModel(Base):
    """One user's membership of one conversation.

    The composite primary key is the uniqueness rule: a user cannot be admitted twice, so a
    duplicate invite fails at the database as well as in the aggregate.
    """

    __tablename__ = "conversation_participants"

    conversation_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("conversations.id"), primary_key=True
    )
    user_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, index=True)
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
