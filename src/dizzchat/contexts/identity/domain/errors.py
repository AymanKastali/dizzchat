"""Shared base for Identity domain errors.

Aggregate-specific errors live inside each aggregate package (``user/errors.py``,
``refresh_token/errors.py``) and subclass this, so the API edge can catch the whole family with
one type. These describe domain-level failures, not transport concerns — nothing here imports a
web framework.
"""

from __future__ import annotations


class IdentityError(Exception):
    """Base class for all Identity domain errors."""
