"""TokenPair DTO."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TokenPair:
    """The tokens returned to a client after login or refresh."""

    access_token: str
    refresh_token: str
