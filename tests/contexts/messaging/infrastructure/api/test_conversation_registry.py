"""Unit tests for ConversationRegistry: (un)subscribe fires only on 0<->1 socket transitions."""

from typing import Any, cast
from uuid import uuid4

from fastapi import WebSocket

from dizzchat.contexts.messaging.domain.conversation import ConversationId
from dizzchat.contexts.messaging.infrastructure.inbound.api.realtime import (
    Connection,
    ConnectionManager,
    ConversationRegistry,
)


class RecordingSubscriber:
    """Records the conversations it was asked to (un)subscribe."""

    def __init__(self) -> None:
        self.subscribed: list[ConversationId] = []
        self.unsubscribed: list[ConversationId] = []

    async def subscribe(self, conversation_id: ConversationId) -> None:
        self.subscribed.append(conversation_id)

    async def unsubscribe(self, conversation_id: ConversationId) -> None:
        self.unsubscribed.append(conversation_id)


class FakeSocket:
    async def send_json(self, data: dict[str, Any]) -> None: ...

    async def close(self, code: int) -> None: ...


def _connection() -> Connection:
    return Connection(cast(WebSocket, FakeSocket()))


async def test_first_join_subscribes_and_a_second_does_not() -> None:
    subscriber = RecordingSubscriber()
    registry = ConversationRegistry(ConnectionManager(), subscriber)
    conversation = ConversationId(uuid4())

    await registry.join(conversation, _connection())
    await registry.join(conversation, _connection())

    assert subscriber.subscribed == [conversation]


async def test_only_the_last_leave_unsubscribes() -> None:
    subscriber = RecordingSubscriber()
    manager = ConnectionManager()
    registry = ConversationRegistry(manager, subscriber)
    conversation = ConversationId(uuid4())
    first, second = _connection(), _connection()
    await registry.join(conversation, first)
    await registry.join(conversation, second)

    await registry.leave(conversation, first)
    assert subscriber.unsubscribed == []  # a socket still remains

    await registry.leave(conversation, second)
    assert subscriber.unsubscribed == [conversation]
