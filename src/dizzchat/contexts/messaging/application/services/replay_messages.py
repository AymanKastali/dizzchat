"""Replay-messages use case — the messages a reconnecting client missed, oldest-first."""

from __future__ import annotations

from dizzchat.contexts.messaging.domain.conversation import ConversationId
from dizzchat.contexts.messaging.domain.message import Message, MessageId, MessageRepository

_REPLAY_PAGE_SIZE = 500


class ReplayMessages:
    """Read persisted messages with an ordering id above ``after``, ascending.

    Paginates internally so an arbitrarily long backlog is replayed completely and in order; the
    caller (the socket) streams the result as ``message.new`` frames after (re)joining.
    """

    def __init__(self, messages: MessageRepository) -> None:
        self._messages = messages

    async def execute(
        self, *, conversation_id: ConversationId, after: MessageId | None
    ) -> list[Message]:
        collected: list[Message] = []
        cursor = after
        while True:
            page = await self._messages.list_since(
                conversation_id, after=cursor, limit=_REPLAY_PAGE_SIZE
            )
            if not page:
                break
            collected.extend(page)
            if len(page) < _REPLAY_PAGE_SIZE:
                break
            cursor = page[-1].id
        return collected
