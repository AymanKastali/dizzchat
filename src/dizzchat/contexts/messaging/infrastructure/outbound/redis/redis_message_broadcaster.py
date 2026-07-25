"""Redis publisher implementing the ``MessageBroadcaster`` port.

Publishing only: a persisted message is fanned out by ``PUBLISH``ing it to its conversation
channel. Every replica (including this one) delivers to its own local sockets through its
subscriber, so there is a single uniform delivery path and no double-delivery.
"""

from __future__ import annotations

from redis.asyncio import Redis

from dizzchat.contexts.messaging.domain.conversation import ConversationId
from dizzchat.contexts.messaging.domain.message import Message
from dizzchat.contexts.messaging.infrastructure.outbound.redis.channels import conversation_channel
from dizzchat.contexts.messaging.infrastructure.outbound.redis.message_codec import encode


class RedisMessageBroadcaster:
    """Fans a message out across replicas by publishing it to its conversation channel."""

    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def broadcast(self, conversation_id: ConversationId, message: Message) -> None:
        await self._redis.publish(conversation_channel(conversation_id), encode(message))
