"""GeneratedRefreshToken DTO."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class GeneratedRefreshToken:
    """A freshly minted refresh token: the client string, its ``jti``, and the hash to store."""

    jti: str
    token: str
    token_hash: str
