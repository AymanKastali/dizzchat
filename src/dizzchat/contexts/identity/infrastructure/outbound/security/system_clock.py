"""System implementation of the ``Clock`` port."""

from __future__ import annotations

from datetime import UTC, datetime


class SystemClock:
    """Returns the real wall-clock time in UTC."""

    def now(self) -> datetime:
        return datetime.now(UTC)
