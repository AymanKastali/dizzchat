"""FastAPI dependency wiring (the composition root for the Identity context).

Turns request-scoped state (a DB session) and process singletons (hasher, token service, clock)
into the use-case handlers, and resolves the authenticated principal from the access token.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from dizzchat.config import Settings, get_settings
from dizzchat.contexts.identity.application.errors import InvalidAccessToken
from dizzchat.contexts.identity.application.ports import Clock, TokenService
from dizzchat.contexts.identity.application.services import (
    AuthenticateUser,
    RefreshAccessToken,
    RegisterUser,
)
from dizzchat.contexts.identity.domain.user import PasswordHasher
from dizzchat.contexts.identity.infrastructure.inbound.api.authenticated_user import (
    AuthenticatedUser,
)
from dizzchat.contexts.identity.infrastructure.outbound.persistence import (
    SqlAlchemyRefreshTokenRepository,
    SqlAlchemyUserRepository,
)
from dizzchat.contexts.identity.infrastructure.outbound.security import (
    Argon2PasswordHasher,
    JwtTokenService,
    SystemClock,
)

SettingsDep = Annotated[Settings, Depends(get_settings)]


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """Yield a request-scoped session; commit on success, roll back on error."""
    session_factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    async with session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


SessionDep = Annotated[AsyncSession, Depends(get_session)]


def get_password_hasher() -> PasswordHasher:
    return Argon2PasswordHasher()


def get_clock() -> Clock:
    return SystemClock()


def get_token_service(settings: SettingsDep) -> TokenService:
    return JwtTokenService(
        secret_key=settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
        access_ttl=timedelta(seconds=settings.access_token_ttl_seconds),
    )


HasherDep = Annotated[PasswordHasher, Depends(get_password_hasher)]
ClockDep = Annotated[Clock, Depends(get_clock)]
TokenServiceDep = Annotated[TokenService, Depends(get_token_service)]


def provide_register_user(session: SessionDep, hasher: HasherDep, clock: ClockDep) -> RegisterUser:
    return RegisterUser(SqlAlchemyUserRepository(session), hasher, clock)


def provide_authenticate_user(
    session: SessionDep,
    hasher: HasherDep,
    tokens: TokenServiceDep,
    clock: ClockDep,
    settings: SettingsDep,
) -> AuthenticateUser:
    return AuthenticateUser(
        SqlAlchemyUserRepository(session),
        SqlAlchemyRefreshTokenRepository(session),
        hasher,
        tokens,
        clock,
        timedelta(seconds=settings.refresh_token_ttl_seconds),
    )


def provide_refresh_access_token(
    session: SessionDep,
    tokens: TokenServiceDep,
    clock: ClockDep,
    settings: SettingsDep,
) -> RefreshAccessToken:
    return RefreshAccessToken(
        SqlAlchemyRefreshTokenRepository(session),
        tokens,
        clock,
        timedelta(seconds=settings.refresh_token_ttl_seconds),
    )


_bearer_scheme = HTTPBearer(auto_error=False)
BearerDep = Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)]


def get_current_user(credentials: BearerDep, tokens: TokenServiceDep) -> AuthenticatedUser:
    """Resolve the caller from the bearer access token, or reject with 401."""
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        claims = tokens.decode_access(credentials.credentials)
    except InvalidAccessToken:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid or expired access token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from None
    return AuthenticatedUser(user_id=claims.user_id)


RegisterUserDep = Annotated[RegisterUser, Depends(provide_register_user)]
AuthenticateUserDep = Annotated[AuthenticateUser, Depends(provide_authenticate_user)]
RefreshAccessTokenDep = Annotated[RefreshAccessToken, Depends(provide_refresh_access_token)]
CurrentUser = Annotated[AuthenticatedUser, Depends(get_current_user)]
