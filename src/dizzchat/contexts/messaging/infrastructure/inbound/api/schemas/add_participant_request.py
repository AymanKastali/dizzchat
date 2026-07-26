"""Add-participant request schema."""

from __future__ import annotations

from pydantic import BaseModel, Field


class AddParticipantRequest(BaseModel):
    """The email of the registered user to admit to the conversation."""

    email: str = Field(min_length=3, max_length=320)
