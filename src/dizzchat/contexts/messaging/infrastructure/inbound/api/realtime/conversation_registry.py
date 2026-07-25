"""Coordinates a socket's local registration with Redis conversation (un)subscription.

One registry per replica. A socket joining a conversation is registered for local delivery and, if
it is the first socket for that conversation on this replica, the replica subscribes to the
conversation's Redis channel; the last socket leaving unsubscribes. A single lock serializes
join/leave so the local-set transition and the matching (un)subscribe happen atomically — a rapid
last-leave-then-first-join cannot leave the replica subscribed-but-empty or empty-but-subscribed.
"""

from __future__ import annotations

import asyncio
from typing import Protocol

from dizzchat.contexts.messaging.domain.conversation import ConversationId
from dizzchat.contexts.messaging.infrastructure.inbound.api.realtime.connection_manager import (
    Connection,
    ConnectionManager,
)


class ConversationSubscriber(Protocol):
    """The (un)subscribe half of the per-replica Redis subscriber the registry drives."""

    async def subscribe(self, conversation_id: ConversationId) -> None: ...

    async def unsubscribe(self, conversation_id: ConversationId) -> None: ...


class ConversationRegistry:
    """Registers/removes a socket locally and drives Redis (un)subscription on 0<->1 transitions."""

    def __init__(self, manager: ConnectionManager, subscriber: ConversationSubscriber) -> None:
        self._manager = manager
        self._subscriber = subscriber
        self._lock = asyncio.Lock()

    async def join(self, conversation_id: ConversationId, connection: Connection) -> None:
        """Register a socket; subscribe to the conversation if it is the first on this replica.

        Subscription is awaited before the caller enters its receive loop, so a message this socket
        sends is guaranteed to loop back through Redis. If the subscribe fails, the local
        registration is rolled back and the error re-raised — otherwise the conversation would be
        left registered-but-unsubscribed, and no later join would ever retry the subscribe.
        """
        async with self._lock:
            if not self._manager.register(conversation_id, connection):
                return
            try:
                await self._subscriber.subscribe(conversation_id)
            except Exception:
                self._manager.unregister(conversation_id, connection)
                raise

    async def leave(self, conversation_id: ConversationId, connection: Connection) -> None:
        """Remove a socket; unsubscribe from the conversation once no local socket needs it."""
        async with self._lock:
            if self._manager.unregister(conversation_id, connection):
                await self._subscriber.unsubscribe(conversation_id)
