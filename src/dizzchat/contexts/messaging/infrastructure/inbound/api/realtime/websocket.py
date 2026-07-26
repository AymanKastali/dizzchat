"""The per-conversation WebSocket endpoint: first-message auth, then a send/broadcast loop.

Domain errors are translated to close codes (auth 4401, forbidden 4403) or ``error`` frames here,
because FastAPI's HTTP exception handlers do not apply to WebSocket connections. A failed message
handling (DB or mock-AI) is reported as an ``error`` frame and never drops the socket, and so is a
frame that exceeds the sender's rate limit.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from uuid import UUID, uuid4

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
    NotConversationParticipant,
    ParticipantId,
)
from dizzchat.contexts.messaging.domain.message import (
    ClientMessageId,
    InvalidMessageContent,
    MessageContent,
    MessageId,
    SenderId,
)
from dizzchat.contexts.messaging.infrastructure.inbound.api.realtime import protocol
from dizzchat.contexts.messaging.infrastructure.inbound.api.realtime.connection_manager import (
    Connection,
)
from dizzchat.contexts.messaging.infrastructure.inbound.api.realtime.dependencies import (
    ConversationAccessDep,
    ConversationRegistryDep,
    MessageExchangeDep,
    MessageReplayerDep,
    RateLimiterDep,
)
from dizzchat.contexts.messaging.infrastructure.inbound.api.realtime.protocol import (
    AuthFrame,
    SendMessageFrame,
)
from dizzchat.logging import connection_id_var

logger = logging.getLogger(__name__)

_AUTH_FAILED_CLOSE = 4401
_FORBIDDEN_CLOSE = 4403
_INTERNAL_ERROR_CLOSE = 1011


async def conversation_ws(
    websocket: WebSocket,
    conversation_id: UUID,
    settings: SettingsDep,
    tokens: TokenServiceDep,
    access: ConversationAccessDep,
    exchange: MessageExchangeDep,
    registry: ConversationRegistryDep,
    replayer: MessageReplayerDep,
    limiter: RateLimiterDep,
) -> None:
    await websocket.accept()

    # Correlation id for every log line emitted while serving this socket. Reset on exit so the
    # id never leaks to a task whose context is reused.
    cid_token = connection_id_var.set(uuid4().hex)
    try:
        authenticated = await _authenticate(websocket, tokens, settings.ws_auth_timeout_seconds)
        if authenticated is None:
            return
        claims, token, last_seen_seq = authenticated

        conversation = ConversationId(conversation_id)
        participant_id = ParticipantId(claims.user_id.value)
        try:
            await access.ensure(conversation_id=conversation, participant_id=participant_id)
        except (ConversationNotFound, NotConversationParticipant):
            await websocket.close(code=_FORBIDDEN_CLOSE)
            return

        connection = Connection(websocket)
        await connection.send(protocol.auth_ok())
        try:
            await registry.join(conversation, connection)
        except Exception:
            # The fan-out subscription could not be established (e.g. Redis is down): fail closed
            # rather than serve a socket that would silently miss cross-replica messages. join() has
            # already rolled back its local registration, so there is nothing to leave.
            logger.exception("failed to join conversation for fan-out")
            await connection.close(code=_INTERNAL_ERROR_CLOSE)
            return

        sender_id = SenderId(claims.user_id.value)
        try:
            # Join above turned on live delivery, so no message can be missed (no gap). Replay
            # then re-sends everything past the client's cursor. Delivery is at-least-once and
            # NOT ordered at the seam: a live broadcast (always a higher seq, being newer) can
            # interleave ahead of a lower-seq replay frame. The client must apply each seq at most
            # once (a seen-set, not a high-water mark, which would drop the later lower-seq frames)
            # and order by the seq each frame carries as ``id``.
            await _replay_missed(connection, replayer, conversation, last_seen_seq)
            await _receive_loop(
                websocket, connection, conversation, sender_id, exchange, tokens, token, limiter
            )
        except WebSocketDisconnect:
            pass
        except Exception:
            logger.exception("error while serving the conversation socket")
            await connection.close(code=_INTERNAL_ERROR_CLOSE)
        finally:
            await registry.leave(conversation, connection)
    finally:
        connection_id_var.reset(cid_token)


async def _authenticate(
    websocket: WebSocket, tokens: TokenServiceDep, auth_timeout_seconds: float
) -> tuple[AccessClaims, str, int | None] | None:
    """Validate the first ``auth`` frame; return (claims, token, last_seen_seq) or close 4401."""
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
    return claims, frame.payload.token, frame.payload.last_seen_seq


async def _replay_missed(
    connection: Connection,
    replayer: MessageReplayerDep,
    conversation: ConversationId,
    last_seen_seq: int | None,
) -> None:
    """Stream messages with ``seq > last_seen_seq``, oldest-first, alongside live delivery.

    Only replays when the client supplied a cursor; a fresh connection (``None``) loads its history
    over the REST endpoint instead. Sending ``last_seen_seq=0`` explicitly requests a full replay.
    Live delivery is already active (the socket has joined), so replay frames may interleave with
    live ones — see the at-least-once contract at the call site.
    """
    if last_seen_seq is None:
        return
    missed = await replayer.replay_since(
        conversation_id=conversation, after=MessageId(last_seen_seq)
    )
    for message in missed:
        await connection.send(protocol.message_new(message))


async def _receive_loop(
    websocket: WebSocket,
    connection: Connection,
    conversation: ConversationId,
    sender_id: SenderId,
    exchange: MessageExchangeDep,
    tokens: TokenServiceDep,
    token: str,
    limiter: RateLimiterDep,
) -> None:
    while True:
        try:
            raw: Any = await websocket.receive_json()
        except ValueError:
            # A non-JSON payload. Held as ``None`` rather than answered here, so that it still
            # passes the rate limit below: a flood of garbage costs a client its quota too.
            raw = None

        # Counted per frame, before any parsing, and keyed on the user (``sender_id`` wraps their
        # id), so the quota covers every socket they hold on every replica.
        if not await limiter.allow(sender_id.value):
            await connection.send(protocol.error("rate limit exceeded"))
            continue

        if raw is None:
            await connection.send(protocol.error("invalid JSON"))
            continue

        try:
            frame = SendMessageFrame.model_validate(raw)
            content = MessageContent(frame.payload.content)
        except (ValidationError, InvalidMessageContent):
            await connection.send(protocol.error("invalid message frame"))
            continue

        client_message_id = (
            ClientMessageId(frame.payload.client_message_id)
            if frame.payload.client_message_id is not None
            else None
        )

        # Re-validate the token on every privileged action, so a socket never outlives its access
        # token (e.g. sends after it has expired).
        try:
            tokens.decode_access(token)
        except InvalidAccessToken:
            await websocket.close(code=_AUTH_FAILED_CLOSE)
            return

        try:
            user_message = await exchange.exchange(
                conversation_id=conversation,
                sender_id=sender_id,
                content=content,
                client_message_id=client_message_id,
            )
        except Exception:
            # A DB or mock-AI failure must never drop the socket.
            logger.exception("failed to handle message")
            await connection.send(protocol.error("failed to handle message"))
            continue

        await connection.send(protocol.message_ack(user_message))
