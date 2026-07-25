"""Shared base for Conversations domain errors.

Aggregate-specific errors live inside each aggregate package (``conversation/errors.py``,
``message/errors.py``) and subclass this, so the API edge can catch the whole family with one
type. These describe domain-level failures, not transport concerns — nothing here imports a
web framework.
"""

from __future__ import annotations


class ConversationsError(Exception):
    """Base class for all Conversations domain errors."""
