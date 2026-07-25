"""Token pair response schema."""

from __future__ import annotations

from pydantic import BaseModel


class TokenResponse(BaseModel):
    """The access + refresh tokens issued on login or refresh."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
