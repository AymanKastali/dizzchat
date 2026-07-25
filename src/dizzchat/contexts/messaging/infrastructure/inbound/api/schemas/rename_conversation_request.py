"""Rename-conversation request schema."""

from __future__ import annotations

from pydantic import BaseModel, Field


class RenameConversationRequest(BaseModel):
    """The new title for an existing conversation."""

    title: str = Field(min_length=1, max_length=200)
