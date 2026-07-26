"""The ``RateLimiter`` port the WebSocket receive loop guards itself with.

Declared beside its consumer rather than in ``application/ports.py``, because no use case depends
on it: the limit protects the *transport* (how fast a socket may be written to), not a business
rule. This mirrors ``ConversationSubscriber``, which is likewise declared next to the registry that
drives it while its Redis adapter lives in ``infrastructure/outbound/redis``.

Taking a bare ``UUID`` keeps the limiter free of this context's domain types — a rate limit is
per *user*, so it caps someone across every socket they hold and every replica serving them.
"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID


class RateLimiter(Protocol):
    """Decides whether a user may send another frame right now."""

    async def allow(self, user_id: UUID) -> bool:
        """Record an attempt by ``user_id``; return ``False`` when it exceeds the limit."""
        ...
