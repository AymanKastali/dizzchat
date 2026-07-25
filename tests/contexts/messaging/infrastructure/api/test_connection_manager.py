"""Unit tests for the ConnectionManager (local broadcast + dead-socket cleanup)."""

from datetime import UTC, datetime
from typing import Any, cast
from uuid import uuid4

from fastapi import WebSocket

from dizzchat.contexts.messaging.domain.conversation import ConversationId
from dizzchat.contexts.messaging.domain.message import (
    Message,
    MessageContent,
    MessageId,
    MessageRole,
    SenderId,
)
from dizzchat.contexts.messaging.infrastructure.inbound.api.realtime import (
    Connection,
    ConnectionManager,
)

_NOW = datetime(2024, 1, 1, tzinfo=UTC)


class FakeSocket:
    """Records the JSON frames sent to it."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send_json(self, data: dict[str, Any]) -> None:
        self.sent.append(data)


class FailingSocket:
    """A socket whose send always fails, standing in for a dead connection."""

    async def send_json(self, data: dict[str, Any]) -> None:
        raise RuntimeError("connection closed")


def _message(message_id: int, conversation_id: ConversationId) -> Message:
    return Message(
        id=MessageId(message_id),
        conversation_id=conversation_id,
        sender_id=SenderId(uuid4()),
        role=MessageRole.USER,
        content=MessageContent("hi"),
        created_at=_NOW,
    )


def _connection(socket: object) -> Connection:
    return Connection(cast(WebSocket, socket))


async def test_broadcast_reaches_only_the_conversations_own_sockets() -> None:
    manager = ConnectionManager()
    conversation_a, conversation_b = ConversationId(uuid4()), ConversationId(uuid4())
    socket_a1, socket_a2, socket_b = FakeSocket(), FakeSocket(), FakeSocket()
    manager.register(conversation_a, _connection(socket_a1))
    manager.register(conversation_a, _connection(socket_a2))
    manager.register(conversation_b, _connection(socket_b))

    await manager.broadcast(conversation_a, _message(1, conversation_a))

    assert len(socket_a1.sent) == 1
    assert socket_a1.sent[0]["type"] == "message.new"
    assert socket_a1.sent[0]["payload"]["content"] == "hi"
    assert len(socket_a2.sent) == 1
    assert socket_b.sent == []  # other conversation untouched


async def test_a_dead_socket_is_dropped_and_does_not_block_the_others() -> None:
    manager = ConnectionManager()
    conversation = ConversationId(uuid4())
    alive = FakeSocket()
    manager.register(conversation, _connection(FailingSocket()))
    manager.register(conversation, _connection(alive))

    await manager.broadcast(conversation, _message(1, conversation))
    assert len(alive.sent) == 1  # the live socket still received it

    # The dead socket was removed; a second broadcast reaches only the survivor.
    await manager.broadcast(conversation, _message(2, conversation))
    assert len(alive.sent) == 2


async def test_unregister_removes_a_socket_from_broadcasts() -> None:
    manager = ConnectionManager()
    conversation = ConversationId(uuid4())
    socket = FakeSocket()
    connection = _connection(socket)
    manager.register(conversation, connection)
    manager.unregister(conversation, connection)

    await manager.broadcast(conversation, _message(1, conversation))

    assert socket.sent == []
