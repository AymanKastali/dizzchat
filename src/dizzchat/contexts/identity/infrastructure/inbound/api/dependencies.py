"""FastAPI dependency wiring (the composition root for the Identity context).

Turns request-scoped state (a DB session) and process singletons (hasher, token service, clock)
into the use-case handlers, and resolves the authenticated principal from the access token. The
generic session/clock dependencies come from the shared kernel.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from dizzchat.config import Settings, get_settings
from dizzchat.contexts.identity.application.errors import InvalidAccessToken
from dizzchat.contexts.identity.application.ports import TokenService
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
)
from dizzchat.shared.infrastructure.inbound.api.dependencies import ClockDep, SessionDep

SettingsDep = Annotated[Settings, Depends(get_settings)]


def get_password_hasher() -> PasswordHasher:
    return Argon2PasswordHasher()


def get_token_service(settings: SettingsDep) -> TokenService:
    return JwtTokenService(
        secret_key=settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
        access_ttl=timedelta(seconds=settings.access_token_ttl_seconds),
    )


HasherDep = Annotated[PasswordHasher, Depends(get_password_hasher)]
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
