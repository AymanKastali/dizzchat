"""Redis fixed-window rate limiter implementing the ``RateLimiter`` port.

The counter lives in the Redis every replica already shares for pub/sub, so one user's quota is
enforced across all of their sockets on every replica — which is the point: a per-process counter
would let a client multiply its allowance by reconnecting to another instance.
"""

from __future__ import annotations

import logging
from uuid import UUID

from redis.asyncio import Redis

from dizzchat.shared.application.clock import Clock

logger = logging.getLogger(__name__)


class RedisRateLimiter:
    """Counts a user's attempts per fixed time window, allowing at most ``limit`` of them.

    A ``limit`` of zero or less disables the check entirely, which is what the demo and the tests
    use to opt out.

    Fixed window rather than a sliding one: the counter is a single ``INCR`` against a key that
    expires on its own, where a sliding window (a sorted set trimmed on every call) would cost
    extra round trips and per-request cleanup to buy precision this does not need. The tradeoff is
    real and worth naming — a client that saves its quota for the end of one window and spends the
    next window's immediately can send up to ``2 * limit`` back to back across the boundary.
    """

    def __init__(self, redis: Redis, clock: Clock, *, limit: int, window_seconds: int) -> None:
        self._redis = redis
        self._clock = clock
        self._limit = limit
        self._window_seconds = window_seconds

    async def allow(self, user_id: UUID) -> bool:
        if self._limit <= 0:
            return True

        try:
            count = await self._increment(self._key(user_id))
        except Exception:
            # Fail open. The limit is a protection, not an authorization rule, so an unreachable
            # Redis must not silence a legitimate client. (Mostly theoretical: a socket cannot even
            # join a conversation while Redis is down — the registry fails closed with 1011.)
            logger.warning("rate limiter unavailable; allowing the frame", exc_info=True)
            return True
        return count <= self._limit

    def _key(self, user_id: UUID) -> str:
        # The window number is part of the key, so each window counts on its own key and that key
        # expires by itself — no sweeper, and re-applying the TTL below cannot slide the window
        # forward (which, on a single shared key, would starve a client that keeps sending).
        window = int(self._clock.now().timestamp()) // self._window_seconds
        return f"ratelimit:ws:{user_id}:{window}"

    async def _increment(self, key: str) -> int:
        # One transaction, not two round trips: if the process died between the INCR and the
        # EXPIRE, the key would be left with no TTL and that user would be locked out for good.
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.incr(key)
            pipe.expire(key, self._window_seconds)
            count, _ = await pipe.execute()
        return int(count)
