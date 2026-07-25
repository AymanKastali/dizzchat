"""FastAPI dependency wiring for the WebSocket endpoint (the real-time composition root).

Each leaf collaborator is injected separately so tests can override just the persistence, the mock
AI, the access check, or the broadcaster while keeping local delivery working. Session-scoped
adapters open one transaction per message / per access check, since a long-lived socket cannot
reuse the request-scoped session. The broadcaster and the conversation registry are per-replica
singletons created in the app lifespan and read from ``app.state``.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, WebSocket
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from dizzchat.contexts.messaging.application.ports import (
    AssistantResponder,
    ConversationAccess,
    MessageBroadcaster,
    MessageReplayer,
    MessageWriter,
)
from dizzchat.contexts.messaging.application.services import MessageExchange
from dizzchat.contexts.messaging.infrastructure.inbound.api.realtime.conversation_registry import (
    ConversationRegistry,
)
from dizzchat.contexts.messaging.infrastructure.outbound.assistant import MockAssistantResponder
from dizzchat.contexts.messaging.infrastructure.outbound.persistence import (
    SessionScopedConversationAccess,
    SessionScopedMessageReplayer,
    SessionScopedMessageWriter,
)
from dizzchat.shared.infrastructure.outbound import SystemClock


def provide_message_broadcaster(websocket: WebSocket) -> MessageBroadcaster:
    broadcaster: MessageBroadcaster = websocket.app.state.message_broadcaster
    return broadcaster


MessageBroadcasterDep = Annotated[MessageBroadcaster, Depends(provide_message_broadcaster)]


def provide_conversation_registry(websocket: WebSocket) -> ConversationRegistry:
    registry: ConversationRegistry = websocket.app.state.conversation_registry
    return registry


ConversationRegistryDep = Annotated[ConversationRegistry, Depends(provide_conversation_registry)]


def provide_message_writer(websocket: WebSocket) -> MessageWriter:
    session_factory: async_sessionmaker[AsyncSession] = websocket.app.state.session_factory
    return SessionScopedMessageWriter(session_factory, SystemClock())


def provide_assistant_responder() -> AssistantResponder:
    return MockAssistantResponder()


def provide_conversation_access(websocket: WebSocket) -> ConversationAccess:
    session_factory: async_sessionmaker[AsyncSession] = websocket.app.state.session_factory
    return SessionScopedConversationAccess(session_factory)


def provide_message_replayer(websocket: WebSocket) -> MessageReplayer:
    session_factory: async_sessionmaker[AsyncSession] = websocket.app.state.session_factory
    return SessionScopedMessageReplayer(session_factory)


def provide_message_exchange(
    writer: Annotated[MessageWriter, Depends(provide_message_writer)],
    responder: Annotated[AssistantResponder, Depends(provide_assistant_responder)],
    broadcaster: MessageBroadcasterDep,
) -> MessageExchange:
    return MessageExchange(writer, responder, broadcaster)


MessageExchangeDep = Annotated[MessageExchange, Depends(provide_message_exchange)]
ConversationAccessDep = Annotated[ConversationAccess, Depends(provide_conversation_access)]
MessageReplayerDep = Annotated[MessageReplayer, Depends(provide_message_replayer)]
