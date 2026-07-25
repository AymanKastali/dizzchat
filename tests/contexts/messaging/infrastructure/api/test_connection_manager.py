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
    """Records the JSON frames sent to it and the close code it was closed with."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.closed_with: int | None = None

    async def send_json(self, data: dict[str, Any]) -> None:
        self.sent.append(data)

    async def close(self, code: int) -> None:
        self.closed_with = code


class FailingSocket:
    """A socket whose send always fails, standing in for a dead connection."""

    async def send_json(self, data: dict[str, Any]) -> None:
        raise RuntimeError("connection closed")

    async def close(self, code: int) -> None:
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


def test_register_reports_only_the_first_socket_of_a_conversation() -> None:
    manager = ConnectionManager()
    conversation = ConversationId(uuid4())

    assert manager.register(conversation, _connection(FakeSocket())) is True
    assert manager.register(conversation, _connection(FakeSocket())) is False


def test_unregister_reports_only_the_removal_of_the_last_socket() -> None:
    manager = ConnectionManager()
    conversation = ConversationId(uuid4())
    first, second = _connection(FakeSocket()), _connection(FakeSocket())
    manager.register(conversation, first)
    manager.register(conversation, second)

    assert manager.unregister(conversation, first) is False
    assert manager.unregister(conversation, second) is True
    # The conversation is now empty: registering again reports a first socket.
    assert manager.register(conversation, _connection(FakeSocket())) is True


async def test_close_all_closes_every_socket_across_conversations() -> None:
    manager = ConnectionManager()
    conversation_a, conversation_b = ConversationId(uuid4()), ConversationId(uuid4())
    socket_a, socket_b = FakeSocket(), FakeSocket()
    manager.register(conversation_a, _connection(socket_a))
    manager.register(conversation_b, _connection(socket_b))

    await manager.close_all(code=1001)

    assert socket_a.closed_with == 1001
    assert socket_b.closed_with == 1001
    # Every conversation was forgotten: registering into either reports a first socket again.
    assert manager.register(conversation_a, _connection(FakeSocket())) is True
    assert manager.register(conversation_b, _connection(FakeSocket())) is True


async def test_close_all_survives_a_socket_that_fails_to_close() -> None:
    manager = ConnectionManager()
    conversation = ConversationId(uuid4())
    alive = FakeSocket()
    manager.register(conversation, _connection(FailingSocket()))
    manager.register(conversation, _connection(alive))

    await manager.close_all()

    assert alive.closed_with == 1001  # the good socket was still closed


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
