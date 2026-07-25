"""End-to-end tests for the conversation WebSocket, wired to in-memory fakes (no database).

These are synchronous ``def`` tests: Starlette's ``TestClient`` drives the socket over its own
portal, which must not be nested inside pytest-asyncio's event loop. The app is used *without* its
lifespan (so migrations never run and no Redis is opened); delivery is wired locally by overriding
the broadcaster and the conversation registry with a single real ``ConnectionManager`` and a no-op
subscriber, so a message still reaches the connected socket without cross-replica fan-out.
"""

from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from dizzchat.app import create_app
from dizzchat.config import Settings, get_settings
from dizzchat.contexts.identity.application.dto import AccessClaims
from dizzchat.contexts.identity.application.errors import InvalidAccessToken
from dizzchat.contexts.identity.domain.user import UserId
from dizzchat.contexts.identity.infrastructure.inbound.api.dependencies import get_token_service
from dizzchat.contexts.messaging.domain.conversation import (
    ConversationNotFound,
    NotConversationOwner,
)
from dizzchat.contexts.messaging.infrastructure.inbound.api.realtime import (
    ConnectionManager,
    ConversationRegistry,
)
from dizzchat.contexts.messaging.infrastructure.inbound.api.realtime.dependencies import (
    provide_assistant_responder,
    provide_conversation_access,
    provide_conversation_registry,
    provide_message_broadcaster,
    provide_message_writer,
)
from tests.contexts.messaging.fakes import (
    CannedAssistantResponder,
    FailingAssistantResponder,
    FailingUserWriter,
    FakeMessageWriter,
    NoOpSubscriber,
)

_VALID_TOKEN = "valid-token"


class FakeTokenService:
    """Decodes only ``_VALID_TOKEN``, to a fixed user; anything else is rejected."""

    def __init__(self, user_id: UUID) -> None:
        self._user_id = user_id

    def decode_access(self, token: str) -> AccessClaims:
        if token != _VALID_TOKEN:
            raise InvalidAccessToken()
        return AccessClaims(user_id=UserId(self._user_id))


class ExpiringTokenService:
    """Valid at connect, then 'expires': every decode after the first is rejected."""

    def __init__(self, user_id: UUID) -> None:
        self._user_id = user_id
        self._calls = 0

    def decode_access(self, token: str) -> AccessClaims:
        self._calls += 1
        if self._calls > 1:
            raise InvalidAccessToken()
        return AccessClaims(user_id=UserId(self._user_id))


class FakeConversationAccess:
    """Allows access unless constructed with an error to raise."""

    def __init__(self, error: Exception | None = None) -> None:
        self._error = error

    async def ensure(self, **_: Any) -> None:
        if self._error is not None:
            raise self._error


def _build_app(
    *,
    user_id: UUID,
    tokens: Any = None,
    writer: Any = None,
    responder: Any = None,
    access: Any = None,
    settings: Settings | None = None,
) -> FastAPI:
    app = create_app()
    # Lifespan is not run in these tests, so there is no Redis. A single real ``ConnectionManager``
    # stands in as the broadcaster (local delivery) and backs the registry (with a no-op
    # subscriber), so a message still reaches the connected socket without cross-replica fan-out.
    manager = ConnectionManager()
    app.dependency_overrides[get_token_service] = lambda: tokens or FakeTokenService(user_id)
    app.dependency_overrides[provide_message_writer] = lambda: writer or FakeMessageWriter()
    app.dependency_overrides[provide_assistant_responder] = lambda: (
        responder or CannedAssistantResponder("You said: hi")
    )
    app.dependency_overrides[provide_conversation_access] = lambda: (
        access or FakeConversationAccess()
    )
    app.dependency_overrides[provide_message_broadcaster] = lambda: manager
    app.dependency_overrides[provide_conversation_registry] = lambda: ConversationRegistry(
        manager, NoOpSubscriber()
    )
    if settings is not None:
        app.dependency_overrides[get_settings] = lambda: settings
    return app


def _auth_frame(token: str = _VALID_TOKEN) -> dict[str, Any]:
    return {"type": "auth", "payload": {"token": token}}


def _url(conversation_id: UUID) -> str:
    return f"/ws/conversations/{conversation_id}"


def test_send_persists_broadcasts_and_returns_the_assistant_reply() -> None:
    writer = FakeMessageWriter()
    app = _build_app(user_id=uuid4(), writer=writer)

    with TestClient(app).websocket_connect(_url(uuid4())) as ws:
        ws.send_json(_auth_frame())
        assert ws.receive_json() == {"type": "auth.ok"}

        ws.send_json({"type": "message.send", "payload": {"content": "hi"}})
        user_new = ws.receive_json()
        assistant_new = ws.receive_json()
        ack = ws.receive_json()

    assert user_new["type"] == "message.new"
    assert user_new["payload"]["role"] == "user"
    assert user_new["payload"]["content"] == "hi"
    assert assistant_new["type"] == "message.new"
    assert assistant_new["payload"]["role"] == "assistant"
    assert assistant_new["payload"]["content"] == "You said: hi"
    assert ack["type"] == "message.ack"
    # Both messages were persisted through the writer.
    assert [m.content.value for m in writer.written] == ["hi", "You said: hi"]


def test_a_bad_token_closes_with_4401() -> None:
    app = _build_app(user_id=uuid4())
    with (
        pytest.raises(WebSocketDisconnect) as disconnect,
        TestClient(app).websocket_connect(_url(uuid4())) as ws,
    ):
        ws.send_json(_auth_frame(token="nope"))
        ws.receive_json()
    assert disconnect.value.code == 4401


def test_a_non_auth_first_frame_closes_with_4401() -> None:
    app = _build_app(user_id=uuid4())
    with (
        pytest.raises(WebSocketDisconnect) as disconnect,
        TestClient(app).websocket_connect(_url(uuid4())) as ws,
    ):
        ws.send_json({"type": "message.send", "payload": {"content": "hi"}})
        ws.receive_json()
    assert disconnect.value.code == 4401


def test_a_missing_auth_frame_times_out_and_closes_with_4401() -> None:
    settings = Settings(ws_auth_timeout_seconds=0.05)
    app = _build_app(user_id=uuid4(), settings=settings)
    with (
        pytest.raises(WebSocketDisconnect) as disconnect,
        TestClient(app).websocket_connect(_url(uuid4())) as ws,
    ):
        ws.receive_json()  # never sent auth; the server closes after the timeout
    assert disconnect.value.code == 4401


def test_access_to_another_users_conversation_closes_with_4403() -> None:
    app = _build_app(user_id=uuid4(), access=FakeConversationAccess(NotConversationOwner()))
    with (
        pytest.raises(WebSocketDisconnect) as disconnect,
        TestClient(app).websocket_connect(_url(uuid4())) as ws,
    ):
        ws.send_json(_auth_frame())
        ws.receive_json()
    assert disconnect.value.code == 4403


def test_a_missing_conversation_closes_with_4403() -> None:
    missing = ConversationNotFound(uuid4())
    app = _build_app(user_id=uuid4(), access=FakeConversationAccess(missing))
    with (
        pytest.raises(WebSocketDisconnect) as disconnect,
        TestClient(app).websocket_connect(_url(uuid4())) as ws,
    ):
        ws.send_json(_auth_frame())
        ws.receive_json()
    assert disconnect.value.code == 4403


def test_a_failing_database_write_reports_an_error_and_keeps_the_socket_open() -> None:
    app = _build_app(user_id=uuid4(), writer=FailingUserWriter())
    with TestClient(app).websocket_connect(_url(uuid4())) as ws:
        ws.send_json(_auth_frame())
        assert ws.receive_json() == {"type": "auth.ok"}

        ws.send_json({"type": "message.send", "payload": {"content": "hi"}})
        first = ws.receive_json()
        # The socket is still open: a second send is handled (and fails again the same way).
        ws.send_json({"type": "message.send", "payload": {"content": "again"}})
        second = ws.receive_json()

    assert first["type"] == "error"
    assert second["type"] == "error"


def test_a_failing_assistant_still_delivers_the_user_message_then_an_error() -> None:
    app = _build_app(user_id=uuid4(), responder=FailingAssistantResponder())
    with TestClient(app).websocket_connect(_url(uuid4())) as ws:
        ws.send_json(_auth_frame())
        assert ws.receive_json() == {"type": "auth.ok"}

        ws.send_json({"type": "message.send", "payload": {"content": "hi"}})
        user_new = ws.receive_json()
        error = ws.receive_json()

    assert user_new["type"] == "message.new"
    assert user_new["payload"]["role"] == "user"
    assert error["type"] == "error"


def test_an_expired_token_closes_the_socket_on_the_next_send() -> None:
    # Valid at connect, then the token 'expires'; the next privileged send must close 4401.
    app = _build_app(user_id=uuid4(), tokens=ExpiringTokenService(uuid4()))
    with (
        pytest.raises(WebSocketDisconnect) as disconnect,
        TestClient(app).websocket_connect(_url(uuid4())) as ws,
    ):
        ws.send_json(_auth_frame())
        assert ws.receive_json() == {"type": "auth.ok"}
        ws.send_json({"type": "message.send", "payload": {"content": "hi"}})
        ws.receive_json()
    assert disconnect.value.code == 4401
