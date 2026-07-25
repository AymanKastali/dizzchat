"""Create-conversation request schema."""

from __future__ import annotations

from pydantic import BaseModel, Field


class CreateConversationRequest(BaseModel):
    """The title for a new conversation."""

    title: str = Field(min_length=1, max_length=200)
