"""Map Identity errors to HTTP responses at the API boundary."""

from __future__ import annotations

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from dizzchat.contexts.identity.application.errors import InvalidAccessToken
from dizzchat.contexts.identity.domain.errors import IdentityError
from dizzchat.contexts.identity.domain.refresh_token import InvalidRefreshToken
from dizzchat.contexts.identity.domain.user import (
    EmailAlreadyRegistered,
    InvalidCredentials,
    InvalidEmail,
)

_STATUS_BY_ERROR: dict[type[IdentityError], int] = {
    EmailAlreadyRegistered: status.HTTP_409_CONFLICT,
    InvalidEmail: status.HTTP_422_UNPROCESSABLE_CONTENT,
    InvalidCredentials: status.HTTP_401_UNAUTHORIZED,
    InvalidRefreshToken: status.HTTP_401_UNAUTHORIZED,
    InvalidAccessToken: status.HTTP_401_UNAUTHORIZED,
}


def register_identity_error_handlers(app: FastAPI) -> None:
    """Register a handler per Identity error so services can raise domain errors freely."""

    async def handle(request: Request, exc: Exception) -> JSONResponse:
        status_code = next(
            (code for error_type, code in _STATUS_BY_ERROR.items() if isinstance(exc, error_type)),
            status.HTTP_400_BAD_REQUEST,
        )
        headers = (
            {"WWW-Authenticate": "Bearer"} if status_code == status.HTTP_401_UNAUTHORIZED else None
        )
        return JSONResponse(status_code=status_code, content={"detail": str(exc)}, headers=headers)

    for error_type in _STATUS_BY_ERROR:
        app.add_exception_handler(error_type, handle)
