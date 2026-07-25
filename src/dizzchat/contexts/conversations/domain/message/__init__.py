"""Message aggregate: root, value objects, repository port, and errors."""

from __future__ import annotations

from .errors import InvalidMessageContent
from .message import Message
from .repository import MessageRepository
from .value_objects import MessageContent, MessageId, SenderId

__all__ = [
    "InvalidMessageContent",
    "Message",
    "MessageContent",
    "MessageId",
    "MessageRepository",
    "SenderId",
]
