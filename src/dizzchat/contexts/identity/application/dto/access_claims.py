"""AccessClaims DTO."""

from __future__ import annotations

from dataclasses import dataclass

from dizzchat.contexts.identity.domain.user import UserId


@dataclass(frozen=True, slots=True)
class AccessClaims:
    """The verified identity carried by an access token."""

    user_id: UserId
