"""Shared outbound infrastructure adapters reused across bounded contexts."""

from __future__ import annotations

from .system_clock import SystemClock

__all__ = ["SystemClock"]
