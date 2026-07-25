"""Shared application-layer ports reused across bounded contexts."""

from __future__ import annotations

from .clock import Clock

__all__ = ["Clock"]
