"""Per-replica registry of live WebSocket connections, grouped by conversation.

Implements the ``MessageBroadcaster`` port for local (in-process) delivery. In Slice 5 a Redis
subscriber will call the same ``broadcast`` to fan a message out to every replica's local sockets;
nothing else about this class changes.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import WebSocket

from dizzchat.contexts.messaging.domain.conversation import ConversationId
from dizzchat.contexts.messaging.domain.message import Message
from dizzchat.contexts.messaging.infrastructure.inbound.api.realtime import protocol

logger = logging.getLogger(__name__)


class Connection:
    """One live socket, whose writes are serialized by a lock.

    Starlette does not serialize concurrent sends, and a single socket can be written to by its own
    receive loop (``ack``/``error``) and by a broadcast at the same time, so every send goes through
    this lock to keep frames from interleaving.
    """

    def __init__(self, websocket: WebSocket) -> None:
        self._websocket = websocket
        self._lock = asyncio.Lock()

    async def send(self, frame: dict[str, Any]) -> None:
        async with self._lock:
            await self._websocket.send_json(frame)


class ConnectionManager:
    """Tracks ``conversation_id -> live connections`` and broadcasts messages to them."""

    def __init__(self) -> None:
        self._connections: dict[ConversationId, set[Connection]] = {}

    def register(self, conversation_id: ConversationId, connection: Connection) -> None:
        self._connections.setdefault(conversation_id, set()).add(connection)

    def unregister(self, conversation_id: ConversationId, connection: Connection) -> None:
        connections = self._connections.get(conversation_id)
        if connections is None:
            return
        connections.discard(connection)
        if not connections:
            del self._connections[conversation_id]

    async def broadcast(self, conversation_id: ConversationId, message: Message) -> None:
        connections = self._connections.get(conversation_id)
        if not connections:
            return
        frame = protocol.message_new(message)
        # Iterate a snapshot so cleanup can mutate the live set; drop any connection that fails to
        # receive (a dead socket) so it doesn't leak or block future broadcasts.
        dead: list[Connection] = []
        for connection in list(connections):
            try:
                await connection.send(frame)
            except Exception:
                logger.warning("dropping dead socket during broadcast", exc_info=True)
                dead.append(connection)
        for connection in dead:
            self.unregister(conversation_id, connection)
