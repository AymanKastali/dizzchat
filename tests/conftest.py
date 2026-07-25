"""Shared test setup.

The connection strings have no code defaults (they are environment-specific), so provide
throwaway values here before the app is imported. Most tests never open these connections; they
only need ``Settings`` to construct. The ``redis_url`` fixture is the exception: it spins a real
Redis via testcontainers for the cross-instance fan-out test, and skips when Docker is unavailable.
"""

import os
from collections.abc import Iterator

import pytest

os.environ.setdefault(
    "DATABASE_URL", "postgresql+asyncpg://dizzchat:dizzchat@localhost:5432/dizzchat"
)
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-that-is-at-least-32-bytes-long")


@pytest.fixture(scope="session")
def redis_url() -> Iterator[str]:
    """A URL for a real, throwaway Redis; skips the test when Docker is not available."""
    try:
        from testcontainers.community.redis import RedisContainer
    except ImportError:  # pragma: no cover - dev dependency missing
        pytest.skip("testcontainers is not installed")

    try:
        container = RedisContainer("redis:7-alpine")
        container.start()
    except Exception as exc:  # pragma: no cover - Docker not available in this environment
        pytest.skip(f"docker unavailable for the redis testcontainer: {exc}")

    try:
        host = container.get_container_host_ip()
        port = container.get_exposed_port(6379)
        yield f"redis://{host}:{port}/0"
    finally:
        container.stop()
