"""Errors raised by the Conversation aggregate."""

from __future__ import annotations

from dizzchat.contexts.messaging.domain.errors import MessagingError


class InvalidConversationTitle(MessagingError):
    """Raised when a string cannot be a valid conversation title (empty or too long)."""

    def __init__(self, value: str) -> None:
        super().__init__(f"invalid conversation title: {value!r}")


class ConversationNotFound(MessagingError):
    """Raised when no active conversation exists for a given id."""

    def __init__(self, conversation_id: object) -> None:
        super().__init__(f"conversation not found: {conversation_id}")


class NotConversationOwner(MessagingError):
    """Raised when a caller acts on a conversation they do not own."""

    def __init__(self) -> None:
        super().__init__("conversation is owned by another user")
