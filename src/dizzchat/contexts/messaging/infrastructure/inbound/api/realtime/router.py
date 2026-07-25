"""Wires the conversation WebSocket onto its route."""

from __future__ import annotations

from fastapi import APIRouter

from dizzchat.contexts.messaging.infrastructure.inbound.api.realtime.websocket import (
    conversation_ws,
)

ws_router = APIRouter()
ws_router.add_api_websocket_route("/ws/conversations/{conversation_id}", conversation_ws)
