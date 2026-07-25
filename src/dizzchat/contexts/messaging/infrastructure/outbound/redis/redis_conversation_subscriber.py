"""Per-replica Redis subscriber that feeds fanned-out messages into local delivery.

One subscriber runs per replica. It (un)subscribes to per-conversation channels as the replica's
local sockets join and leave, and a single reader task decodes each received message and hands it
to the local ``ConnectionManager`` for delivery. If the Redis connection drops, the reader
reconnects with a short backoff and re-subscribes to the channels it still needs.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

from redis.asyncio import Redis

from dizzchat.contexts.messaging.domain.conversation import ConversationId
from dizzchat.contexts.messaging.infrastructure.inbound.api.realtime.connection_manager import (
    ConnectionManager,
)
from dizzchat.contexts.messaging.infrastructure.outbound.redis.channels import conversation_channel
from dizzchat.contexts.messaging.infrastructure.outbound.redis.message_codec import decode

logger = logging.getLogger(__name__)

_READ_TIMEOUT_SECONDS = 1.0
_IDLE_POLL_SECONDS = 0.05
_RECONNECT_BACKOFF_SECONDS = 0.5


class RedisConversationSubscriber:
    """Subscribes to conversation channels and delivers received messages to local sockets."""

    def __init__(self, redis: Redis, connection_manager: ConnectionManager) -> None:
        self._redis = redis
        self._pubsub = redis.pubsub()
        self._manager = connection_manager
        self._lock = asyncio.Lock()
        self._channels: set[str] = set()
        self._task: asyncio.Task[None] | None = None
        self._running = False

    async def start(self) -> None:
        """Start the background reader task."""
        self._running = True
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        """Stop the reader task and close the pub/sub connection."""
        self._running = False
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        await self._pubsub.aclose()  # type: ignore[no-untyped-call]  # redis ships aclose unannotated

    async def subscribe(self, conversation_id: ConversationId) -> None:
        """Start receiving a conversation's fanned-out messages on this replica."""
        channel = conversation_channel(conversation_id)
        async with self._lock:
            # Subscribe (which establishes the pub/sub connection) before recording the channel, so
            # the reader never sees a channel it can read yet on a connection-less pub/sub.
            await self._pubsub.subscribe(channel)
            self._channels.add(channel)

    async def unsubscribe(self, conversation_id: ConversationId) -> None:
        """Stop receiving a conversation's messages once no local socket needs them."""
        channel = conversation_channel(conversation_id)
        async with self._lock:
            self._channels.discard(channel)
            await self._pubsub.unsubscribe(channel)

    async def _run(self) -> None:
        while self._running:
            # ``get_message`` errors on a pub/sub with no connection, which is the case until the
            # first subscribe (and again if every channel is later dropped), so idle until there is
            # something to read.
            if not self._channels:
                await asyncio.sleep(_IDLE_POLL_SECONDS)
                continue
            try:
                message = await self._pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=_READ_TIMEOUT_SECONDS
                )
            except Exception:
                logger.warning("redis subscriber read failed; reconnecting", exc_info=True)
                await self._reconnect()
                continue
            if message is None or message.get("type") != "message":
                continue
            await self._deliver(message["data"])

    async def _deliver(self, data: bytes) -> None:
        try:
            message = decode(data)
        except Exception:
            logger.exception("failed to decode a fanned-out message")
            return
        await self._manager.broadcast(message.conversation_id, message)

    async def _reconnect(self) -> None:
        await asyncio.sleep(_RECONNECT_BACKOFF_SECONDS)
        async with self._lock:
            with contextlib.suppress(Exception):
                await self._pubsub.aclose()  # type: ignore[no-untyped-call]
            self._pubsub = self._redis.pubsub()
            for channel in self._channels:
                await self._pubsub.subscribe(channel)
