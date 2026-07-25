"""MessagePage DTO — one page of cursor-paginated conversation history."""

from __future__ import annotations

from dataclasses import dataclass

from dizzchat.contexts.conversations.domain.message import Message, MessageId


@dataclass(frozen=True, slots=True)
class MessagePage:
    """A page of messages, newest first.

    ``next_cursor`` is the id to pass as ``before`` to fetch the next (older) page; it is set
    only when ``has_more`` is true.
    """

    items: tuple[Message, ...]
    next_cursor: MessageId | None
    has_more: bool
