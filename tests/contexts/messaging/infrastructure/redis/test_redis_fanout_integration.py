"""The strong-signal test: a message published by one replica reaches a socket on another.

Two replicas (each a ConnectionManager + subscriber + registry) share one real Redis. A message
published from replica A must be delivered to a socket registered on replica B (cross-instance
fan-out) and to a socket on replica A itself (loopback through Redis).
"""

import asyncio
from dataclasses import dataclass, field
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
    ConversationRegistry,
)
from dizzchat.contexts.messaging.infrastructure.outbound.redis import (
    RedisConversationSubscriber,
    RedisMessageBroadcaster,
)
from dizzchat.shared.infrastructure.outbound.redis_client import create_redis_client


@dataclass
class FakeSocket:
    sent: list[dict[str, Any]] = field(default_factory=list)

    async def send_json(self, data: dict[str, Any]) -> None:
        self.sent.append(data)

    async def close(self, code: int) -> None: ...


def _connection(socket: FakeSocket) -> Connection:
    return Connection(cast(WebSocket, socket))


def _message(conversation_id: ConversationId, content: str = "hello across replicas") -> Message:
    return Message(
        id=MessageId(1),
        conversation_id=conversation_id,
        sender_id=SenderId(uuid4()),
        role=MessageRole.USER,
        content=MessageContent(content),
        created_at=datetime(2024, 1, 1, tzinfo=UTC),
    )


async def _wait_for_frame(socket: FakeSocket, deadline_seconds: float = 3.0) -> dict[str, Any]:
    elapsed = 0.0
    while elapsed < deadline_seconds:
        if socket.sent:
            return socket.sent[0]
        await asyncio.sleep(0.05)
        elapsed += 0.05
    raise AssertionError("no frame was delivered before the deadline")


async def test_a_message_from_one_replica_reaches_a_socket_on_another(redis_url: str) -> None:
    conversation = ConversationId(uuid4())
    client_a = create_redis_client(redis_url)
    client_b = create_redis_client(redis_url)

    manager_a, manager_b = ConnectionManager(), ConnectionManager()
    subscriber_a = RedisConversationSubscriber(client_a, manager_a)
    subscriber_b = RedisConversationSubscriber(client_b, manager_b)
    registry_a = ConversationRegistry(manager_a, subscriber_a)
    registry_b = ConversationRegistry(manager_b, subscriber_b)
    broadcaster = RedisMessageBroadcaster(client_a)

    socket_a, socket_b = FakeSocket(), FakeSocket()
    try:
        await subscriber_a.start()
        await subscriber_b.start()
        await registry_a.join(conversation, _connection(socket_a))
        await registry_b.join(conversation, _connection(socket_b))

        await broadcaster.broadcast(conversation, _message(conversation))

        frame_b = await _wait_for_frame(socket_b)
        frame_a = await _wait_for_frame(socket_a)
    finally:
        await subscriber_a.stop()
        await subscriber_b.stop()
        await client_a.aclose()
        await client_b.aclose()

    assert frame_b["type"] == "message.new"
    assert frame_b["payload"]["content"] == "hello across replicas"
    assert frame_a["type"] == "message.new"  # loopback: the producing replica delivers locally too


async def test_a_replica_only_receives_conversations_it_subscribed_to(redis_url: str) -> None:
    subscribed = ConversationId(uuid4())
    other = ConversationId(uuid4())
    client_pub = create_redis_client(redis_url)
    client_sub = create_redis_client(redis_url)
    manager = ConnectionManager()
    subscriber = RedisConversationSubscriber(client_sub, manager)
    registry = ConversationRegistry(manager, subscriber)
    broadcaster = RedisMessageBroadcaster(client_pub)

    socket = FakeSocket()
    try:
        await subscriber.start()
        await registry.join(subscribed, _connection(socket))

        # Publish to the un-subscribed conversation first, then to the subscribed one. Gating on
        # the second (which must arrive) proves the first had its chance and was correctly dropped.
        await broadcaster.broadcast(other, _message(other, content="should not arrive"))
        await broadcaster.broadcast(subscribed, _message(subscribed, content="should arrive"))

        frame = await _wait_for_frame(socket)
    finally:
        await subscriber.stop()
        await client_pub.aclose()
        await client_sub.aclose()

    assert len(socket.sent) == 1  # only the subscribed conversation's message was delivered
    assert frame["payload"]["content"] == "should arrive"
