"""Signup request schema."""

from __future__ import annotations

from pydantic import BaseModel, Field


class SignupRequest(BaseModel):
    """Email + password for a new account."""

    email: str = Field(min_length=1, max_length=320)
    # Cap the length: argon2 hashes the input in full, so an unbounded password is a cheap
    # CPU-amplification vector on this unauthenticated endpoint.
    password: str = Field(min_length=1, max_length=1024)
