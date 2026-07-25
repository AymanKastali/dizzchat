"""Real-time messaging inbound adapter: the WebSocket endpoint, protocol, and connection manager."""

from __future__ import annotations

from .connection_manager import Connection, ConnectionManager
from .conversation_registry import ConversationRegistry
from .router import ws_router

__all__ = ["Connection", "ConnectionManager", "ConversationRegistry", "ws_router"]
