"""Contract tests for the dedupe and forward-replay reads the app services rely on."""

from datetime import UTC, datetime
from uuid import uuid4

from dizzchat.contexts.messaging.domain.conversation import ConversationId
from dizzchat.contexts.messaging.domain.message import (
    ClientMessageId,
    MessageContent,
    MessageId,
    MessageRole,
)
from tests.contexts.messaging.fakes import FakeMessageRepository

_NOW = datetime(2024, 1, 1, tzinfo=UTC)


async def test_find_by_client_message_id_returns_the_stored_message() -> None:
    repo = FakeMessageRepository()
    conversation_id = ConversationId(uuid4())
    client_message_id = ClientMessageId(uuid4())

    created = await repo.create(
        conversation_id=conversation_id,
        sender_id=None,
        role=MessageRole.USER,
        content=MessageContent("a"),
        created_at=_NOW,
        client_message_id=client_message_id,
    )

    assert await repo.find_by_client_message_id(conversation_id, client_message_id) == created
    assert await repo.find_by_client_message_id(conversation_id, ClientMessageId(uuid4())) is None


async def test_list_since_returns_ids_above_the_cursor_ascending() -> None:
    repo = FakeMessageRepository()
    conversation_id = ConversationId(uuid4())
    for _ in range(3):
        await repo.create(
            conversation_id=conversation_id,
            sender_id=None,
            role=MessageRole.USER,
            content=MessageContent("x"),
            created_at=_NOW,
        )

    since = await repo.list_since(conversation_id, after=MessageId(1), limit=10)

    assert [m.id.value for m in since] == [2, 3]
