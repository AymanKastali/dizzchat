"""The ``Clock`` port — a generic technical capability shared across bounded contexts.

Concrete adapters live in ``shared/infrastructure/outbound``. Injecting the clock keeps
time-dependent logic testable (a fixed clock in tests, the wall clock in production).
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol


class Clock(Protocol):
    """A source of the current time, injected so time-dependent logic stays testable."""

    def now(self) -> datetime:
        """Return the current timezone-aware UTC time."""
        ...
