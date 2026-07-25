"""Errors raised by the Message aggregate."""

from __future__ import annotations

from dizzchat.contexts.messaging.domain.errors import MessagingError


class InvalidMessageContent(MessagingError):
    """Raised when a message's content is empty."""

    def __init__(self) -> None:
        super().__init__("message content must not be empty")
