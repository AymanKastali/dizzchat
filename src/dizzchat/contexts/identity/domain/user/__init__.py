"""User aggregate: root, value objects, repository port, hashing port, and errors."""

from __future__ import annotations

from .errors import EmailAlreadyRegistered, InvalidCredentials, InvalidEmail
from .password_hasher import PasswordHasher
from .repository import UserRepository
from .user import User
from .value_objects import Email, PasswordHash, UserId

__all__ = [
    "Email",
    "EmailAlreadyRegistered",
    "InvalidCredentials",
    "InvalidEmail",
    "PasswordHash",
    "PasswordHasher",
    "User",
    "UserId",
    "UserRepository",
]
