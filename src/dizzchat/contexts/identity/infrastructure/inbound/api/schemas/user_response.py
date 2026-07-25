"""User response schema."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class UserResponse(BaseModel):
    """A registered user, as returned to clients."""

    id: UUID
    email: str
    created_at: datetime
