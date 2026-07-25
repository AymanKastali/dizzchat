"""FastAPI dependency wiring for the WebSocket endpoint (the real-time composition root).

Each leaf collaborator is injected separately so tests can override just the persistence, the mock
AI, or the access check while keeping the real ``ConnectionManager`` (so broadcasts still reach the
connected socket). Session-scoped adapters open one transaction per message / per access check,
since a long-lived socket cannot reuse the request-scoped session.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, WebSocket
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from dizzchat.contexts.messaging.application.ports import (
    AssistantResponder,
    ConversationAccess,
    MessageWriter,
)
from dizzchat.contexts.messaging.application.services import MessageExchange
from dizzchat.contexts.messaging.infrastructure.inbound.api.realtime.connection_manager import (
    ConnectionManager,
)
from dizzchat.contexts.messaging.infrastructure.outbound.assistant import MockAssistantResponder
from dizzchat.contexts.messaging.infrastructure.outbound.persistence import (
    SessionScopedConversationAccess,
    SessionScopedMessageWriter,
)
from dizzchat.shared.infrastructure.outbound import SystemClock


def provide_connection_manager(websocket: WebSocket) -> ConnectionManager:
    manager: ConnectionManager = websocket.app.state.connection_manager
    return manager


ConnectionManagerDep = Annotated[ConnectionManager, Depends(provide_connection_manager)]


def provide_message_writer(websocket: WebSocket) -> MessageWriter:
    session_factory: async_sessionmaker[AsyncSession] = websocket.app.state.session_factory
    return SessionScopedMessageWriter(session_factory, SystemClock())


def provide_assistant_responder() -> AssistantResponder:
    return MockAssistantResponder()


def provide_conversation_access(websocket: WebSocket) -> ConversationAccess:
    session_factory: async_sessionmaker[AsyncSession] = websocket.app.state.session_factory
    return SessionScopedConversationAccess(session_factory)


def provide_message_exchange(
    writer: Annotated[MessageWriter, Depends(provide_message_writer)],
    responder: Annotated[AssistantResponder, Depends(provide_assistant_responder)],
    manager: ConnectionManagerDep,
) -> MessageExchange:
    return MessageExchange(writer, responder, manager)


MessageExchangeDep = Annotated[MessageExchange, Depends(provide_message_exchange)]
ConversationAccessDep = Annotated[ConversationAccess, Depends(provide_conversation_access)]
