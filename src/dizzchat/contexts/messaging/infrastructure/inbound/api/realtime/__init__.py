"""Real-time messaging inbound adapter: the WebSocket endpoint, protocol, and connection manager."""

from __future__ import annotations

from .connection_manager import ConnectionManager
from .router import ws_router

__all__ = ["ConnectionManager", "ws_router"]
