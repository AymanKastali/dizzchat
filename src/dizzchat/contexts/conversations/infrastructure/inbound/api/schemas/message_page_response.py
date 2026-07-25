"""Message-page response schema (one page of cursor-paginated history)."""

from __future__ import annotations

from pydantic import BaseModel

from dizzchat.contexts.conversations.application.dto import MessagePage
from dizzchat.contexts.conversations.infrastructure.inbound.api.schemas.message_response import (
    MessageResponse,
)


class MessagePageResponse(BaseModel):
    """A page of messages, newest first, with the cursor for the next (older) page."""

    items: list[MessageResponse]
    next_cursor: int | None
    has_more: bool

    @classmethod
    def from_domain(cls, page: MessagePage) -> MessagePageResponse:
        return cls(
            items=[MessageResponse.from_domain(message) for message in page.items],
            next_cursor=page.next_cursor.value if page.next_cursor is not None else None,
            has_more=page.has_more,
        )
