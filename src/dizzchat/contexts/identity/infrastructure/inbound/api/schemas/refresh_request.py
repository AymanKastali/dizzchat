"""Refresh request schema."""

from __future__ import annotations

from pydantic import BaseModel, Field


class RefreshRequest(BaseModel):
    """A refresh token to exchange for a fresh token pair."""

    refresh_token: str = Field(min_length=1)
