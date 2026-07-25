"""Shared async Redis client factory.

The client is created once at startup (see the app lifespan) and shared across the pub/sub
publisher and the per-replica subscriber that fan messages out across replicas.
"""

from __future__ import annotations

from redis.asyncio import Redis, from_url


def create_redis_client(redis_url: str) -> Redis:
    """Create the async Redis client for the given URL.

    ``decode_responses`` is left off: pub/sub payloads are delivered as the raw bytes the message
    codec produced, and the codec owns decoding them back into a domain ``Message``.
    """
    return from_url(redis_url)
