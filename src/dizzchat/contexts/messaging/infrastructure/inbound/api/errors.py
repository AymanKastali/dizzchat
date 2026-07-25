"""Map Conversations errors to HTTP responses at the API boundary."""

from __future__ import annotations

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from dizzchat.contexts.messaging.domain.conversation import (
    ConversationNotFound,
    InvalidConversationTitle,
    NotConversationOwner,
)
from dizzchat.contexts.messaging.domain.errors import MessagingError
from dizzchat.contexts.messaging.domain.message import InvalidMessageContent

_STATUS_BY_ERROR: dict[type[MessagingError], int] = {
    ConversationNotFound: status.HTTP_404_NOT_FOUND,
    NotConversationOwner: status.HTTP_403_FORBIDDEN,
    InvalidConversationTitle: status.HTTP_422_UNPROCESSABLE_CONTENT,
    InvalidMessageContent: status.HTTP_422_UNPROCESSABLE_CONTENT,
}


def register_messaging_error_handlers(app: FastAPI) -> None:
    """Register a handler per Conversations error so services can raise domain errors freely."""

    async def handle(request: Request, exc: Exception) -> JSONResponse:
        status_code = next(
            (code for error_type, code in _STATUS_BY_ERROR.items() if isinstance(exc, error_type)),
            status.HTTP_400_BAD_REQUEST,
        )
        return JSONResponse(status_code=status_code, content={"detail": str(exc)})

    for error_type in _STATUS_BY_ERROR:
        app.add_exception_handler(error_type, handle)
