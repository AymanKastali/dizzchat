"""SQLAlchemy adapter implementing the domain ``RefreshTokenRepository`` port."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dizzchat.contexts.identity.domain.refresh_token import RefreshToken
from dizzchat.contexts.identity.domain.user import UserId
from dizzchat.contexts.identity.infrastructure.outbound.persistence.models import RefreshTokenModel


class SqlAlchemyRefreshTokenRepository:
    """Persists the ``RefreshToken`` aggregate, translating between domain and row model."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, token: RefreshToken) -> None:
        self._session.add(_to_model(token))

    async def get_by_jti(self, jti: str) -> RefreshToken | None:
        result = await self._session.execute(
            select(RefreshTokenModel).where(RefreshTokenModel.jti == jti)
        )
        model = result.scalar_one_or_none()
        return _to_domain(model) if model is not None else None

    async def save(self, token: RefreshToken) -> None:
        # The domain object was loaded detached; merge upserts the current state (e.g. revocation).
        await self._session.merge(_to_model(token))


def _to_model(token: RefreshToken) -> RefreshTokenModel:
    return RefreshTokenModel(
        jti=token.jti,
        user_id=token.user_id.value,
        token_hash=token.token_hash,
        expires_at=token.expires_at,
        revoked_at=token.revoked_at,
    )


def _to_domain(model: RefreshTokenModel) -> RefreshToken:
    return RefreshToken(
        jti=model.jti,
        user_id=UserId(model.user_id),
        token_hash=model.token_hash,
        expires_at=model.expires_at,
        revoked_at=model.revoked_at,
    )
