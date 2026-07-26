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


class NotConversationParticipant(MessagingError):
    """Raised when a caller reads from or posts to a conversation they do not take part in."""

    def __init__(self) -> None:
        super().__init__("caller is not a participant of this conversation")


class ParticipantUserNotFound(MessagingError):
    """Raised when an invited email belongs to no registered user.

    A Messaging error rather than an Identity one: the failure is "this conversation cannot add
    that participant", so translating it here keeps the context's error surface self-contained.
    """

    def __init__(self, email: str) -> None:
        super().__init__(f"no registered user for email: {email}")


class CannotRemoveConversationOwner(MessagingError):
    """Raised when removing the owner from their own conversation.

    The owner is a participant by construction; removing them would leave a conversation its own
    owner could neither read nor post to while still being able to rename and delete it.
    """

    def __init__(self) -> None:
        super().__init__("the conversation owner cannot be removed")
