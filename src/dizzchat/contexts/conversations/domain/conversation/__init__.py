"""Conversation aggregate: root, value objects, repository port, and errors."""

from __future__ import annotations

from .conversation import Conversation
from .errors import ConversationNotFound, InvalidConversationTitle, NotConversationOwner
from .repository import ConversationRepository
from .value_objects import ConversationId, ConversationTitle, OwnerId

__all__ = [
    "Conversation",
    "ConversationId",
    "ConversationNotFound",
    "ConversationRepository",
    "ConversationTitle",
    "InvalidConversationTitle",
    "NotConversationOwner",
    "OwnerId",
]
