"""Integration tests for the Redis rate limiter, against a real Redis.

A fake cannot prove what matters here: that the counter is *shared*, so a user's quota holds across
replicas. The last test runs two limiter instances (standing in for two app replicas) against one
Redis and shows the second seeing the first's count.

The clock is injected and moved by hand, so the window-rollover test is deterministic instead of
sleeping through a real window.
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from redis.asyncio import Redis

from dizzchat.contexts.messaging.infrastructure.outbound.redis import RedisRateLimiter
from dizzchat.shared.infrastructure.outbound.redis_client import create_redis_client

_NOW = datetime(2024, 1, 1, 12, 0, tzinfo=UTC)


class MovableClock:
    """A clock the test advances explicitly, to cross a window boundary without sleeping."""

    def __init__(self, now: datetime) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += timedelta(seconds=seconds)


@pytest.fixture
async def redis(redis_url: str) -> Redis:
    client = create_redis_client(redis_url)
    await client.flushdb()
    return client


async def test_allows_up_to_the_limit_then_denies(redis: Redis) -> None:
    limiter = RedisRateLimiter(redis, MovableClock(_NOW), limit=3, window_seconds=10)
    user = uuid4()

    assert [await limiter.allow(user) for _ in range(4)] == [True, True, True, False]


async def test_each_user_has_their_own_quota(redis: Redis) -> None:
    limiter = RedisRateLimiter(redis, MovableClock(_NOW), limit=1, window_seconds=10)
    alice, bob = uuid4(), uuid4()

    assert await limiter.allow(alice) is True
    assert await limiter.allow(alice) is False
    # Bob is keyed separately, so alice exhausting hers does not touch his.
    assert await limiter.allow(bob) is True


async def test_a_new_window_restores_the_quota(redis: Redis) -> None:
    clock = MovableClock(_NOW)
    limiter = RedisRateLimiter(redis, clock, limit=1, window_seconds=10)
    user = uuid4()

    assert await limiter.allow(user) is True
    assert await limiter.allow(user) is False

    clock.advance(10)
    assert await limiter.allow(user) is True


async def test_the_counter_key_expires_on_its_own(redis: Redis) -> None:
    limiter = RedisRateLimiter(redis, MovableClock(_NOW), limit=5, window_seconds=10)
    user = uuid4()

    await limiter.allow(user)

    keys = [key async for key in redis.scan_iter(match="ratelimit:ws:*")]
    assert len(keys) == 1
    # A TTL is set, so an idle user's counters are reclaimed without a sweeper.
    assert 0 < await redis.ttl(keys[0]) <= 10


async def test_a_limit_of_zero_disables_the_check(redis: Redis) -> None:
    limiter = RedisRateLimiter(redis, MovableClock(_NOW), limit=0, window_seconds=10)
    user = uuid4()

    assert [await limiter.allow(user) for _ in range(5)] == [True] * 5
    # Nothing was even counted: the disabled limiter never touches Redis.
    assert [key async for key in redis.scan_iter(match="ratelimit:ws:*")] == []


async def test_an_unreachable_redis_fails_open(caplog: pytest.LogCaptureFixture) -> None:
    # A dead port, not a closed client: redis-py reconnects a closed pool transparently, so
    # closing one would let the command succeed and the test would pass for the wrong reason.
    client = create_redis_client("redis://127.0.0.1:1/0", socket_connect_timeout=0.2)
    limiter = RedisRateLimiter(client, MovableClock(_NOW), limit=1, window_seconds=10)

    try:
        with caplog.at_level("WARNING"):
            # A protection, not an authorization rule: an infra failure must not silence a client.
            assert await limiter.allow(uuid4()) is True
    finally:
        await client.aclose()

    # And it is not silent — failing open is a degraded mode operators need to see.
    assert "rate limiter unavailable" in caplog.text


async def test_the_quota_is_shared_across_replicas(redis: Redis, redis_url: str) -> None:
    """Two limiters on one Redis share a user's quota — the 2+ replica requirement."""
    clock = MovableClock(_NOW)
    replica_one = RedisRateLimiter(redis, clock, limit=2, window_seconds=10)
    other_client = create_redis_client(redis_url)
    replica_two = RedisRateLimiter(other_client, clock, limit=2, window_seconds=10)
    user = uuid4()

    try:
        assert await replica_one.allow(user) is True
        assert await replica_two.allow(user) is True
        # The third attempt is refused by *either* replica: the count is one shared counter, so a
        # client cannot reset its quota by reconnecting to the other instance.
        assert await replica_two.allow(user) is False
        assert await replica_one.allow(user) is False
    finally:
        await other_client.aclose()
