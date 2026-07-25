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

    claims = await _authenticate(websocket, tokens, settings.ws_auth_timeout_seconds)
    if claims is None:
        return

    conversation = ConversationId(conversation_id)
    owner_id = OwnerId(claims.user_id.value)
    try:
        await access.ensure(conversation_id=conversation, owner_id=owner_id)
    except (ConversationNotFound, NotConversationOwner):
        await websocket.close(code=_FORBIDDEN_CLOSE)
        return

    await websocket.send_json(protocol.auth_ok())
    manager.register(conversation, websocket)
    sender_id = SenderId(claims.user_id.value)
    try:
        await _receive_loop(websocket, conversation, sender_id, exchange)
    except WebSocketDisconnect:
        pass
    finally:
        manager.unregister(conversation, websocket)


async def _authenticate(
    websocket: WebSocket, tokens: TokenServiceDep, auth_timeout_seconds: float
) -> AccessClaims | None:
    """Read and validate the first ``auth`` frame, or close 4401 and return ``None``."""
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
    return claims


async def _receive_loop(
    websocket: WebSocket,
    conversation: ConversationId,
    sender_id: SenderId,
    exchange: MessageExchangeDep,
) -> None:
    while True:
        try:
            raw = await websocket.receive_json()
        except ValueError:
            await websocket.send_json(protocol.error("invalid JSON"))
            continue

        try:
            frame = SendMessageFrame.model_validate(raw)
            content = MessageContent(frame.payload.content)
        except (ValidationError, InvalidMessageContent):
            await websocket.send_json(protocol.error("invalid message frame"))
            continue

        try:
            user_message = await exchange.exchange(
                conversation_id=conversation, sender_id=sender_id, content=content
            )
        except Exception:
            # A DB or mock-AI failure must never drop the socket.
            logger.exception("failed to handle message")
            await websocket.send_json(protocol.error("failed to handle message"))
            continue

        await websocket.send_json(protocol.message_ack(user_message))
