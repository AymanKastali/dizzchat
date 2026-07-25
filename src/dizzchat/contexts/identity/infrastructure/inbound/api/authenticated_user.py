"""The authenticated principal resolved from an access token."""

from __future__ import annotations

from dataclasses import dataclass

from dizzchat.contexts.identity.domain.user import UserId


@dataclass(frozen=True, slots=True)
class AuthenticatedUser:
    """The identity of the caller behind a validated access token."""

    user_id: UserId
