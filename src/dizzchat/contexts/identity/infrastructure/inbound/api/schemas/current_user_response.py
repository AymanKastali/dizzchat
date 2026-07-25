"""Current-user response schema (from the access token, no database lookup)."""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel


class CurrentUserResponse(BaseModel):
    """The identity carried by the presented access token."""

    user_id: UUID
