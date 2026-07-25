"""Per-replica registry of live WebSocket connections, grouped by conversation.

Implements the ``MessageBroadcaster`` port for local (in-process) delivery. In Slice 5 a Redis
subscriber will call the same ``broadcast`` to fan a message out to every replica's local sockets;
nothing else about this class changes.
"""

from __future__ import annotations

import logging

from fastapi import WebSocket

from dizzchat.contexts.messaging.domain.conversation import ConversationId
from dizzchat.contexts.messaging.domain.message import Message
from dizzchat.contexts.messaging.infrastructure.inbound.api.realtime import protocol

logger = logging.getLogger(__name__)


class ConnectionManager:
    """Tracks ``conversation_id -> live sockets`` and broadcasts messages to them."""

    def __init__(self) -> None:
        self._connections: dict[ConversationId, set[WebSocket]] = {}

    def register(self, conversation_id: ConversationId, websocket: WebSocket) -> None:
        self._connections.setdefault(conversation_id, set()).add(websocket)

    def unregister(self, conversation_id: ConversationId, websocket: WebSocket) -> None:
        sockets = self._connections.get(conversation_id)
        if sockets is None:
            return
        sockets.discard(websocket)
        if not sockets:
            del self._connections[conversation_id]

    async def broadcast(self, conversation_id: ConversationId, message: Message) -> None:
        sockets = self._connections.get(conversation_id)
        if not sockets:
            return
        frame = protocol.message_new(message)
        # Iterate a snapshot so cleanup can mutate the live set; drop any socket that fails to
        # receive (a dead connection) so it doesn't leak or block future broadcasts.
        dead: list[WebSocket] = []
        for websocket in list(sockets):
            try:
                await websocket.send_json(frame)
            except Exception:
                logger.warning("dropping dead socket during broadcast", exc_info=True)
                dead.append(websocket)
        for websocket in dead:
            self.unregister(conversation_id, websocket)
