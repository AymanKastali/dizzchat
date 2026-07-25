"""The JSON WebSocket message protocol: inbound frame models and outbound frame builders.

Envelope shape: ``{"type": ..., "payload": {...}}`` for data frames, ``{"type": "error",
"error": ...}`` for failures. Inbound frames are validated with Pydantic; outbound frames are
plain JSON-ready dicts for ``websocket.send_json``.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

from dizzchat.contexts.messaging.domain.message import Message


class AuthPayload(BaseModel):
    token: str


class AuthFrame(BaseModel):
    """First frame the client must send: the access token to authenticate the connection."""

    type: Literal["auth"]
    payload: AuthPayload


class SendMessagePayload(BaseModel):
    content: str


class SendMessageFrame(BaseModel):
    """A message the client sends into the conversation."""

    type: Literal["message.send"]
    payload: SendMessagePayload


InboundFrame = Annotated[AuthFrame | SendMessageFrame, Field(discriminator="type")]


def auth_ok() -> dict[str, Any]:
    return {"type": "auth.ok"}


def message_new(message: Message) -> dict[str, Any]:
    return {"type": "message.new", "payload": _message_payload(message)}


def message_ack(message: Message) -> dict[str, Any]:
    return {
        "type": "message.ack",
        "payload": {"id": message.id.value, "created_at": message.created_at.isoformat()},
    }


def error(detail: str) -> dict[str, Any]:
    return {"type": "error", "error": detail}


def _message_payload(message: Message) -> dict[str, Any]:
    return {
        "id": message.id.value,
        "conversation_id": str(message.conversation_id.value),
        "sender_id": str(message.sender_id.value) if message.sender_id is not None else None,
        "role": message.role.value,
        "content": message.content.value,
        "created_at": message.created_at.isoformat(),
    }
