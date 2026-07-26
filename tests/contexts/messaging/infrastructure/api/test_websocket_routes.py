"""End-to-end tests for the conversation WebSocket, wired to in-memory fakes (no database).

These are synchronous ``def`` tests: Starlette's ``TestClient`` drives the socket over its own
portal, which must not be nested inside pytest-asyncio's event loop. The app is used *without* its
lifespan (so migrations never run and no Redis is opened); delivery is wired locally by overriding
the broadcaster and the conversation registry with a single real ``ConnectionManager`` and a no-op
subscriber, so a message still reaches the connected socket without cross-replica fan-out.
"""

from datetime import UTC, datetime
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
    ConversationId,
    ConversationNotFound,
    NotConversationParticipant,
)
from dizzchat.contexts.messaging.domain.message import (
    Message,
    MessageContent,
    MessageId,
    MessageRole,
    SenderId,
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
    provide_message_replayer,
    provide_message_writer,
)
from tests.contexts.messaging.fakes import (
    CannedAssistantResponder,
    FailingAssistantResponder,
    FailingUserWriter,
    FakeMessageWriter,
    NoOpSubscriber,
    StubMessageReplayer,
)

_VALID_TOKEN = "valid-token"
_SECOND_USER_TOKEN = "valid-token-2"


class FakeTokenService:
    """Decodes ``_VALID_TOKEN`` to ``user_id``, plus any ``extra`` token→user pairs.

    ``extra`` is what lets one app authenticate two *different* users, so a test can put two
    people in the same conversation.
    """

    def __init__(self, user_id: UUID, extra: dict[str, UUID] | None = None) -> None:
        self._by_token = {_VALID_TOKEN: user_id, **(extra or {})}

    def decode_access(self, token: str) -> AccessClaims:
        user_id = self._by_token.get(token)
        if user_id is None:
            raise InvalidAccessToken()
        return AccessClaims(user_id=UserId(user_id))


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
    replayer: Any = None,
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
    app.dependency_overrides[provide_message_replayer] = lambda: replayer or StubMessageReplayer()
    if settings is not None:
        app.dependency_overrides[get_settings] = lambda: settings
    return app


def _auth_frame(token: str = _VALID_TOKEN, last_seen_seq: int | None = None) -> dict[str, Any]:
    return {"type": "auth", "payload": {"token": token, "last_seen_seq": last_seen_seq}}


def _url(conversation_id: UUID) -> str:
    return f"/ws/conversations/{conversation_id}"


def _replayed_message(seq: int, content: str) -> Message:
    return Message(
        id=MessageId(seq),
        conversation_id=ConversationId(uuid4()),
        sender_id=SenderId(uuid4()),
        role=MessageRole.USER,
        content=MessageContent(content),
        created_at=datetime(2024, 1, 1, tzinfo=UTC),
    )


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


def test_a_message_from_one_user_is_broadcast_to_every_other_user_in_the_conversation() -> None:
    """The multi-user requirement, end to end through the socket layer.

    Two *different* authenticated users join one conversation, and a message either of them sends
    reaches both. Alice additionally gets her own ``message.ack``, which Bob does not.
    """
    alice, bob = uuid4(), uuid4()
    app = _build_app(user_id=alice, tokens=FakeTokenService(alice, {_SECOND_USER_TOKEN: bob}))
    conversation = uuid4()
    client = TestClient(app)

    with (
        client.websocket_connect(_url(conversation)) as alice_ws,
        client.websocket_connect(_url(conversation)) as bob_ws,
    ):
        alice_ws.send_json(_auth_frame())
        assert alice_ws.receive_json() == {"type": "auth.ok"}
        bob_ws.send_json(_auth_frame(token=_SECOND_USER_TOKEN))
        assert bob_ws.receive_json() == {"type": "auth.ok"}

        alice_ws.send_json({"type": "message.send", "payload": {"content": "hi"}})

        alice_frames = [alice_ws.receive_json() for _ in range(3)]
        bob_frames = [bob_ws.receive_json() for _ in range(2)]

    # Bob sees Alice's message and the assistant reply, but never an ack for a send he didn't make.
    assert [(f["type"], f["payload"]["content"]) for f in bob_frames] == [
        ("message.new", "hi"),
        ("message.new", "You said: hi"),
    ]
    assert [f["type"] for f in alice_frames] == ["message.new", "message.new", "message.ack"]
    assert [f["payload"]["sender_id"] for f in bob_frames] == [str(alice), None]


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


def test_access_to_a_conversation_you_are_not_in_closes_with_4403() -> None:
    app = _build_app(user_id=uuid4(), access=FakeConversationAccess(NotConversationParticipant()))
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


def test_a_send_with_a_client_message_id_acks_with_the_echoed_id_and_seq() -> None:
    app = _build_app(user_id=uuid4())
    client_message_id = str(uuid4())

    with TestClient(app).websocket_connect(_url(uuid4())) as ws:
        ws.send_json(_auth_frame())
        assert ws.receive_json() == {"type": "auth.ok"}

        ws.send_json(
            {
                "type": "message.send",
                "payload": {"content": "hi", "client_message_id": client_message_id},
            }
        )
        ws.receive_json()  # message.new (user)
        ws.receive_json()  # message.new (assistant)
        ack = ws.receive_json()

    assert ack["type"] == "message.ack"
    assert ack["payload"]["client_message_id"] == client_message_id
    assert isinstance(ack["payload"]["id"], int)  # the ordering seq


def test_a_duplicate_send_yields_a_single_ack_and_no_second_exchange() -> None:
    writer = FakeMessageWriter()
    app = _build_app(user_id=uuid4(), writer=writer)
    client_message_id = str(uuid4())
    payload = {"content": "hi", "client_message_id": client_message_id}

    with TestClient(app).websocket_connect(_url(uuid4())) as ws:
        ws.send_json(_auth_frame())
        assert ws.receive_json() == {"type": "auth.ok"}

        ws.send_json({"type": "message.send", "payload": payload})
        first_user = ws.receive_json()
        ws.receive_json()  # assistant message.new
        first_ack = ws.receive_json()

        # The duplicate is acked without a second message.new (no re-broadcast, no assistant turn).
        ws.send_json({"type": "message.send", "payload": payload})
        second_ack = ws.receive_json()

    assert first_user["type"] == "message.new"
    assert first_ack["type"] == "message.ack"
    assert second_ack["type"] == "message.ack"
    assert second_ack["payload"]["id"] == first_ack["payload"]["id"]  # same server message
    # Only the first send persisted a user + assistant message; the duplicate stored nothing.
    assert [m.role for m in writer.written] == [MessageRole.USER, MessageRole.ASSISTANT]


def test_reconnect_replays_missed_messages_in_order_before_live() -> None:
    replayer = StubMessageReplayer([_replayed_message(2, "second"), _replayed_message(3, "third")])
    app = _build_app(user_id=uuid4(), replayer=replayer)

    with TestClient(app).websocket_connect(_url(uuid4())) as ws:
        ws.send_json(_auth_frame(last_seen_seq=1))
        assert ws.receive_json() == {"type": "auth.ok"}
        first = ws.receive_json()
        second = ws.receive_json()

    assert replayer.replayed_after == MessageId(1)
    assert [first["payload"]["id"], second["payload"]["id"]] == [2, 3]
    assert [first["payload"]["content"], second["payload"]["content"]] == ["second", "third"]


def test_reconnect_with_an_up_to_date_cursor_replays_nothing() -> None:
    replayer = StubMessageReplayer([])
    app = _build_app(user_id=uuid4(), replayer=replayer)

    with TestClient(app).websocket_connect(_url(uuid4())) as ws:
        ws.send_json(_auth_frame(last_seen_seq=5))
        assert ws.receive_json() == {"type": "auth.ok"}

        # No replay frame arrives; the next frame is the live send's own user message.
        ws.send_json({"type": "message.send", "payload": {"content": "hi"}})
        live = ws.receive_json()

    assert replayer.replayed_after == MessageId(5)
    assert live["type"] == "message.new"
    assert live["payload"]["content"] == "hi"
