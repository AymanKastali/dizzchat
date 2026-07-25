"""SQLAlchemy adapter implementing the domain ``UserRepository`` port."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dizzchat.contexts.identity.domain.user import Email, PasswordHash, User, UserId
from dizzchat.contexts.identity.infrastructure.outbound.persistence.models import UserModel


class SqlAlchemyUserRepository:
    """Persists the ``User`` aggregate, translating between domain and row model."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, user: User) -> None:
        self._session.add(_to_model(user))

    async def get_by_email(self, email: Email) -> User | None:
        result = await self._session.execute(
            select(UserModel).where(UserModel.email == email.value)
        )
        model = result.scalar_one_or_none()
        return _to_domain(model) if model is not None else None


def _to_model(user: User) -> UserModel:
    return UserModel(
        id=user.id.value,
        email=user.email.value,
        password_hash=user.password_hash.value,
        created_at=user.created_at,
    )


def _to_domain(model: UserModel) -> User:
    return User(
        id=UserId(model.id),
        email=Email(model.email),
        password_hash=PasswordHash(model.password_hash),
        created_at=model.created_at,
    )
