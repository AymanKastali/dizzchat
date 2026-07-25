"""Shared async Redis client factory.

The client is created once at startup (see the app lifespan) and shared across the pub/sub
publisher and the per-replica subscriber that fan messages out across replicas.
"""

from __future__ import annotations

from redis.asyncio import Redis, from_url

_DEFAULT_TIMEOUT_SECONDS = 5.0


def create_redis_client(
    redis_url: str,
    *,
    socket_timeout: float = _DEFAULT_TIMEOUT_SECONDS,
    socket_connect_timeout: float = _DEFAULT_TIMEOUT_SECONDS,
) -> Redis:
    """Create the async Redis client for the given URL.

    Socket timeouts are bounded so a ``publish`` (awaited inside the message-exchange flow) or a
    connect can never hang the caller's receive loop if Redis becomes unresponsive; the subscriber
    passes its own per-read timeout to ``get_message``, so this does not affect polling.

    ``decode_responses`` is left off: pub/sub payloads are delivered as the raw bytes the message
    codec produced, and the codec owns decoding them back into a domain ``Message``.
    """
    return from_url(
        redis_url,
        socket_timeout=socket_timeout,
        socket_connect_timeout=socket_connect_timeout,
    )
