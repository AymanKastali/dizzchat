"""The per-conversation WebSocket endpoint: first-message auth, then a send/broadcast loop.

Domain errors are translated to close codes (auth 4401, forbidden 4403) or ``error`` frames here,
because FastAPI's HTTP exception handlers do not apply to WebSocket connections. A failed message
handling (DB or mock-AI) is reported as an ``error`` frame and never drops the socket.
"""

from __future__ import annotations

import asyncio
import logging
from uuid import UUID

from fastapi import WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from dizzchat.contexts.identity.application.dto import AccessClaims
from dizzchat.contexts.identity.application.errors import InvalidAccessToken
from dizzchat.contexts.identity.infrastructure.inbound.api.dependencies import (
    SettingsDep,
    TokenServiceDep,
)
from dizzchat.contexts.messaging.domain.conversation import (
    ConversationId,
    ConversationNotFound,
    NotConversationOwner,
    OwnerId,
)
from dizzchat.contexts.messaging.domain.message import (
    InvalidMessageContent,
    MessageContent,
    SenderId,
)
from dizzchat.contexts.messaging.infrastructure.inbound.api.realtime import protocol
from dizzchat.contexts.messaging.infrastructure.inbound.api.realtime.connection_manager import (
    Connection,
)
from dizzchat.contexts.messaging.infrastructure.inbound.api.realtime.dependencies import (
    ConnectionManagerDep,
    ConversationAccessDep,
    MessageExchangeDep,
)
from dizzchat.contexts.messaging.infrastructure.inbound.api.realtime.protocol import (
    AuthFrame,
    SendMessageFrame,
)

logger = logging.getLogger(__name__)

_AUTH_FAILED_CLOSE = 4401
_FORBIDDEN_CLOSE = 4403


async def conversation_ws(
    websocket: WebSocket,
    conversation_id: UUID,
    settings: SettingsDep,
    tokens: TokenServiceDep,
    access: ConversationAccessDep,
    exchange: MessageExchangeDep,
    manager: ConnectionManagerDep,
) -> None:
    await websocket.accept()

    authenticated = await _authenticate(websocket, tokens, settings.ws_auth_timeout_seconds)
    if authenticated is None:
        return
    claims, token = authenticated

    conversation = ConversationId(conversation_id)
    owner_id = OwnerId(claims.user_id.value)
    try:
        await access.ensure(conversation_id=conversation, owner_id=owner_id)
    except (ConversationNotFound, NotConversationOwner):
        await websocket.close(code=_FORBIDDEN_CLOSE)
        return

    connection = Connection(websocket)
    await connection.send(protocol.auth_ok())
    manager.register(conversation, connection)
    sender_id = SenderId(claims.user_id.value)
    try:
        await _receive_loop(websocket, connection, conversation, sender_id, exchange, tokens, token)
    except WebSocketDisconnect:
        pass
    finally:
        manager.unregister(conversation, connection)


async def _authenticate(
    websocket: WebSocket, tokens: TokenServiceDep, auth_timeout_seconds: float
) -> tuple[AccessClaims, str] | None:
    """Read and validate the first ``auth`` frame; return (claims, token) or close 4401."""
    try:
        raw = await asyncio.wait_for(websocket.receive_json(), timeout=auth_timeout_seconds)
    except WebSocketDisconnect:
        return None
    except (TimeoutError, ValueError):
        # No frame in time, or a non-JSON payload.
        await websocket.close(code=_AUTH_FAILED_CLOSE)
        return None

    try:
        frame = AuthFrame.model_validate(raw)
        claims = tokens.decode_access(frame.payload.token)
    except (ValidationError, InvalidAccessToken):
        await websocket.close(code=_AUTH_FAILED_CLOSE)
        return None
    return claims, frame.payload.token


async def _receive_loop(
    websocket: WebSocket,
    connection: Connection,
    conversation: ConversationId,
    sender_id: SenderId,
    exchange: MessageExchangeDep,
    tokens: TokenServiceDep,
    token: str,
) -> None:
    while True:
        try:
            raw = await websocket.receive_json()
        except ValueError:
            await connection.send(protocol.error("invalid JSON"))
            continue

        try:
            frame = SendMessageFrame.model_validate(raw)
            content = MessageContent(frame.payload.content)
        except (ValidationError, InvalidMessageContent):
            await connection.send(protocol.error("invalid message frame"))
            continue

        # Re-validate the token on every privileged action, so a socket never outlives its access
        # token (e.g. sends after it has expired).
        try:
            tokens.decode_access(token)
        except InvalidAccessToken:
            await websocket.close(code=_AUTH_FAILED_CLOSE)
            return

        try:
            user_message = await exchange.exchange(
                conversation_id=conversation, sender_id=sender_id, content=content
            )
        except Exception:
            # A DB or mock-AI failure must never drop the socket.
            logger.exception("failed to handle message")
            await connection.send(protocol.error("failed to handle message"))
            continue

        await connection.send(protocol.message_ack(user_message))
