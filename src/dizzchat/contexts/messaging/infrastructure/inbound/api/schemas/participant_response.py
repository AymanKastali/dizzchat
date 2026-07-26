"""Participant response schema."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from dizzchat.contexts.messaging.domain.conversation import Participant


class ParticipantResponse(BaseModel):
    """One conversation membership as returned to clients."""

    user_id: UUID
    joined_at: datetime

    @classmethod
    def from_domain(cls, participant: Participant) -> ParticipantResponse:
        return cls(user_id=participant.id.value, joined_at=participant.joined_at)
