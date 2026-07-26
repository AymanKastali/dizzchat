"""Conversation aggregate: root, value objects, repository port, and errors."""

from __future__ import annotations

from .conversation import Conversation
from .errors import (
    CannotRemoveConversationOwner,
    ConversationNotFound,
    InvalidConversationTitle,
    NotConversationOwner,
    NotConversationParticipant,
    ParticipantUserNotFound,
)
from .participant import Participant
from .repository import ConversationRepository
from .value_objects import ConversationId, ConversationTitle, OwnerId, ParticipantId

__all__ = [
    "CannotRemoveConversationOwner",
    "Conversation",
    "ConversationId",
    "ConversationNotFound",
    "ConversationRepository",
    "ConversationTitle",
    "InvalidConversationTitle",
    "NotConversationOwner",
    "NotConversationParticipant",
    "OwnerId",
    "Participant",
    "ParticipantId",
    "ParticipantUserNotFound",
]
