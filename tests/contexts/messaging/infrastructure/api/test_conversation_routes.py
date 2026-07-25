"""End-to-end API tests for the conversation routes, wired to in-memory fakes (no database)."""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from dizzchat.app import create_app
from dizzchat.contexts.identity.domain.user import UserId
from dizzchat.contexts.identity.infrastructure.inbound.api.authenticated_user import (
    AuthenticatedUser,
)
from dizzchat.contexts.identity.infrastructure.inbound.api.dependencies import get_current_user
from dizzchat.contexts.messaging.application.services import (
    CreateConversation,
    DeleteConversation,
    GetConversationHistory,
    ListConversations,
    RenameConversation,
)
from dizzchat.contexts.messaging.domain.conversation import (
    ConversationId,
    ConversationTitle,
    OwnerId,
)
from dizzchat.contexts.messaging.domain.message import MessageContent, SenderId
from dizzchat.contexts.messaging.infrastructure.inbound.api.dependencies import (
    provide_create_conversation,
    provide_delete_conversation,
    provide_get_conversation_history,
    provide_list_conversations,
    provide_rename_conversation,
)
from tests.contexts.messaging.fakes import (
    FakeConversationRepository,
    FakeMessageRepository,
    FixedClock,
)

_NOW = datetime(2024, 1, 1, tzinfo=UTC)


def _build_app(
    conversations: FakeConversationRepository,
    messages: FakeMessageRepository,
    caller_id: UUID,
    *,
    authenticated: bool,
) -> FastAPI:
    app = create_app()
    clock = FixedClock(_NOW)
    app.dependency_overrides[provide_create_conversation] = lambda: CreateConversation(
        conversations, clock
    )
    app.dependency_overrides[provide_list_conversations] = lambda: ListConversations(conversations)
    app.dependency_overrides[provide_rename_conversation] = lambda: RenameConversation(
        conversations, clock
    )
    app.dependency_overrides[provide_delete_conversation] = lambda: DeleteConversation(
        conversations, clock
    )
    app.dependency_overrides[provide_get_conversation_history] = lambda: GetConversationHistory(
        conversations, messages
    )
    if authenticated:
        app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
            user_id=UserId(caller_id)
        )
    return app


@dataclass
class _Context:
    client: AsyncClient
    conversations: FakeConversationRepository
    messages: FakeMessageRepository
    caller_id: UUID


@pytest.fixture
async def ctx() -> AsyncIterator[_Context]:
    conversations = FakeConversationRepository()
    messages = FakeMessageRepository()
    caller_id = uuid4()
    app = _build_app(conversations, messages, caller_id, authenticated=True)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield _Context(client, conversations, messages, caller_id)


async def _seed_conversation(
    conversations: FakeConversationRepository, owner_id: UUID
) -> ConversationId:
    conversation_id = ConversationId(uuid4())
    await conversations.create(
        conversation_id=conversation_id,
        owner_id=OwnerId(owner_id),
        title=ConversationTitle("seeded"),
        created_at=_NOW,
        updated_at=_NOW,
    )
    return conversation_id


async def test_create_conversation(ctx: _Context) -> None:
    response = await ctx.client.post("/conversations", json={"title": "Planning"})

    assert response.status_code == 201
    body = response.json()
    assert body["title"] == "Planning"
    assert body["owner_id"] == str(ctx.caller_id)
    assert "id" in body


async def test_create_rejects_a_blank_title(ctx: _Context) -> None:
    response = await ctx.client.post("/conversations", json={"title": ""})

    assert response.status_code == 422


async def test_list_returns_the_callers_conversations(ctx: _Context) -> None:
    # (Newest-first ordering is covered in the application test, where timestamps differ; here the
    # fixed clock makes both created_at equal, so only membership is asserted.)
    await ctx.client.post("/conversations", json={"title": "first"})
    await ctx.client.post("/conversations", json={"title": "second"})

    response = await ctx.client.get("/conversations")

    assert response.status_code == 200
    titles = {c["title"] for c in response.json()}
    assert titles == {"first", "second"}


async def test_rename_conversation(ctx: _Context) -> None:
    created = await ctx.client.post("/conversations", json={"title": "old"})
    conversation_id = created.json()["id"]

    response = await ctx.client.patch(f"/conversations/{conversation_id}", json={"title": "new"})

    assert response.status_code == 200
    assert response.json()["title"] == "new"


async def test_delete_soft_deletes_and_hides_the_conversation(ctx: _Context) -> None:
    created = await ctx.client.post("/conversations", json={"title": "x"})
    conversation_id = created.json()["id"]

    deleted = await ctx.client.delete(f"/conversations/{conversation_id}")
    assert deleted.status_code == 204

    listed = await ctx.client.get("/conversations")
    assert listed.json() == []


async def test_rename_a_conversation_owned_by_another_is_forbidden(ctx: _Context) -> None:
    conversation_id = await _seed_conversation(ctx.conversations, owner_id=uuid4())

    response = await ctx.client.patch(f"/conversations/{conversation_id}", json={"title": "hijack"})

    assert response.status_code == 403


async def test_rename_a_missing_conversation_is_not_found(ctx: _Context) -> None:
    response = await ctx.client.patch(f"/conversations/{uuid4()}", json={"title": "x"})

    assert response.status_code == 404


async def test_history_is_cursor_paginated(ctx: _Context) -> None:
    conversation_id = await _seed_conversation(ctx.conversations, owner_id=ctx.caller_id)
    for i in range(5):
        await ctx.messages.create(
            conversation_id=conversation_id,
            sender_id=SenderId(ctx.caller_id),
            content=MessageContent(f"m{i}"),
            created_at=_NOW,
        )

    first = await ctx.client.get(f"/conversations/{conversation_id}/messages?limit=3")
    assert first.status_code == 200
    first_body = first.json()
    assert [m["content"] for m in first_body["items"]] == ["m4", "m3", "m2"]
    assert first_body["has_more"] is True
    assert first_body["next_cursor"] == 3

    cursor = first_body["next_cursor"]
    second = await ctx.client.get(
        f"/conversations/{conversation_id}/messages?limit=3&before={cursor}"
    )
    second_body = second.json()
    assert [m["content"] for m in second_body["items"]] == ["m1", "m0"]
    assert second_body["has_more"] is False
    assert second_body["next_cursor"] is None


async def test_routes_require_authentication() -> None:
    conversations = FakeConversationRepository()
    messages = FakeMessageRepository()
    app = _build_app(conversations, messages, uuid4(), authenticated=False)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/conversations")

    assert response.status_code == 401
