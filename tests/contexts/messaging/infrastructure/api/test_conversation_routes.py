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
    AddParticipant,
    CreateConversation,
    DeleteConversation,
    GetConversationHistory,
    ListConversations,
    ListParticipants,
    RemoveParticipant,
    RenameConversation,
    RestoreConversation,
)
from dizzchat.contexts.messaging.domain.conversation import (
    ConversationId,
    ConversationTitle,
    OwnerId,
    ParticipantId,
)
from dizzchat.contexts.messaging.domain.message import MessageContent, MessageRole, SenderId
from dizzchat.contexts.messaging.infrastructure.inbound.api.dependencies import (
    provide_add_participant,
    provide_create_conversation,
    provide_delete_conversation,
    provide_get_conversation_history,
    provide_list_conversations,
    provide_list_participants,
    provide_remove_participant,
    provide_rename_conversation,
    provide_restore_conversation,
)
from tests.contexts.messaging.fakes import (
    FakeConversationRepository,
    FakeMessageRepository,
    FakeUserDirectory,
    FixedClock,
)

_NOW = datetime(2024, 1, 1, tzinfo=UTC)


def _build_app(
    conversations: FakeConversationRepository,
    messages: FakeMessageRepository,
    caller_id: UUID,
    *,
    authenticated: bool,
    users: FakeUserDirectory | None = None,
) -> FastAPI:
    app = create_app()
    clock = FixedClock(_NOW)
    directory = users if users is not None else FakeUserDirectory()
    app.dependency_overrides[provide_add_participant] = lambda: AddParticipant(
        conversations, directory, clock
    )
    app.dependency_overrides[provide_list_participants] = lambda: ListParticipants(conversations)
    app.dependency_overrides[provide_remove_participant] = lambda: RemoveParticipant(conversations)
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
    app.dependency_overrides[provide_restore_conversation] = lambda: RestoreConversation(
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
    users: FakeUserDirectory


@pytest.fixture
async def ctx() -> AsyncIterator[_Context]:
    conversations = FakeConversationRepository()
    messages = FakeMessageRepository()
    caller_id = uuid4()
    users = FakeUserDirectory()
    app = _build_app(conversations, messages, caller_id, authenticated=True, users=users)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield _Context(client, conversations, messages, caller_id, users)


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
        participant_ids=frozenset({ParticipantId(owner_id)}),
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


async def test_restore_brings_a_deleted_conversation_back(ctx: _Context) -> None:
    created = await ctx.client.post("/conversations", json={"title": "x"})
    conversation_id = created.json()["id"]
    await ctx.client.delete(f"/conversations/{conversation_id}")

    restored = await ctx.client.post(f"/conversations/{conversation_id}/restore")

    assert restored.status_code == 200
    assert restored.json()["id"] == conversation_id
    listed = await ctx.client.get("/conversations")
    assert [c["id"] for c in listed.json()] == [conversation_id]


async def test_restore_is_idempotent_and_accepts_an_active_conversation(ctx: _Context) -> None:
    created = await ctx.client.post("/conversations", json={"title": "x"})
    conversation_id = created.json()["id"]
    await ctx.client.delete(f"/conversations/{conversation_id}")

    first = await ctx.client.post(f"/conversations/{conversation_id}/restore")
    second = await ctx.client.post(f"/conversations/{conversation_id}/restore")

    assert (first.status_code, second.status_code) == (200, 200)
    assert first.json() == second.json()


async def test_restoring_a_conversation_owned_by_another_is_forbidden(ctx: _Context) -> None:
    conversation_id = await _seed_conversation(ctx.conversations, owner_id=uuid4())
    await ctx.conversations.update(
        conversation_id=conversation_id,
        title=ConversationTitle("seeded"),
        updated_at=_NOW,
        deleted_at=_NOW,
    )

    response = await ctx.client.post(f"/conversations/{conversation_id}/restore")

    assert response.status_code == 403


async def test_restoring_a_conversation_that_never_existed_is_not_found(ctx: _Context) -> None:
    response = await ctx.client.post(f"/conversations/{uuid4()}/restore")

    assert response.status_code == 404


async def test_a_restored_conversation_serves_its_history_again(ctx: _Context) -> None:
    created = await ctx.client.post("/conversations", json={"title": "x"})
    conversation_id = created.json()["id"]
    await ctx.messages.create(
        conversation_id=ConversationId(UUID(conversation_id)),
        sender_id=SenderId(ctx.caller_id),
        role=MessageRole.USER,
        content=MessageContent("kept through the delete"),
        created_at=_NOW,
    )
    await ctx.client.delete(f"/conversations/{conversation_id}")
    assert (await ctx.client.get(f"/conversations/{conversation_id}/messages")).status_code == 404

    await ctx.client.post(f"/conversations/{conversation_id}/restore")

    history = await ctx.client.get(f"/conversations/{conversation_id}/messages")
    assert history.status_code == 200
    assert [m["content"] for m in history.json()["items"]] == ["kept through the delete"]


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
            role=MessageRole.USER,
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


async def test_history_for_someone_who_is_not_a_participant_is_forbidden(ctx: _Context) -> None:
    conversation_id = await _seed_conversation(ctx.conversations, owner_id=uuid4())

    response = await ctx.client.get(f"/conversations/{conversation_id}/messages")

    assert response.status_code == 403


async def test_owner_adds_a_participant_who_can_then_read_the_conversation(
    ctx: _Context,
) -> None:
    created = await ctx.client.post("/conversations", json={"title": "room"})
    conversation_id = created.json()["id"]
    guest = uuid4()
    ctx.users.register("guest@example.com", guest)

    added = await ctx.client.post(
        f"/conversations/{conversation_id}/participants", json={"email": "guest@example.com"}
    )
    assert added.status_code == 204

    listed = await ctx.client.get(f"/conversations/{conversation_id}/participants")
    assert listed.status_code == 200
    assert {p["user_id"] for p in listed.json()} == {str(ctx.caller_id), str(guest)}


async def test_adding_the_same_participant_twice_is_idempotent(ctx: _Context) -> None:
    created = await ctx.client.post("/conversations", json={"title": "room"})
    conversation_id = created.json()["id"]
    ctx.users.register("guest@example.com", uuid4())
    body = {"email": "guest@example.com"}

    first = await ctx.client.post(f"/conversations/{conversation_id}/participants", json=body)
    second = await ctx.client.post(f"/conversations/{conversation_id}/participants", json=body)

    assert (first.status_code, second.status_code) == (204, 204)
    listed = await ctx.client.get(f"/conversations/{conversation_id}/participants")
    assert len(listed.json()) == 2  # the owner plus one guest, not two guest rows


async def test_adding_an_unregistered_email_is_not_found(ctx: _Context) -> None:
    created = await ctx.client.post("/conversations", json={"title": "room"})
    conversation_id = created.json()["id"]

    response = await ctx.client.post(
        f"/conversations/{conversation_id}/participants", json={"email": "nobody@example.com"}
    )

    assert response.status_code == 404


async def test_only_the_owner_may_add_a_participant(ctx: _Context) -> None:
    conversation_id = await _seed_conversation(ctx.conversations, owner_id=uuid4())
    ctx.users.register("guest@example.com", uuid4())

    response = await ctx.client.post(
        f"/conversations/{conversation_id}/participants", json={"email": "guest@example.com"}
    )

    assert response.status_code == 403


async def test_owner_removes_a_participant(ctx: _Context) -> None:
    created = await ctx.client.post("/conversations", json={"title": "room"})
    conversation_id = created.json()["id"]
    guest = uuid4()
    ctx.users.register("guest@example.com", guest)
    await ctx.client.post(
        f"/conversations/{conversation_id}/participants", json={"email": "guest@example.com"}
    )

    removed = await ctx.client.delete(f"/conversations/{conversation_id}/participants/{guest}")

    assert removed.status_code == 204
    listed = await ctx.client.get(f"/conversations/{conversation_id}/participants")
    assert [p["user_id"] for p in listed.json()] == [str(ctx.caller_id)]


async def test_a_participant_may_remove_themselves(ctx: _Context) -> None:
    owner_id = uuid4()
    conversation_id = await _seed_conversation(ctx.conversations, owner_id=owner_id)
    await ctx.conversations.add_participant(
        conversation_id=conversation_id,
        participant_id=ParticipantId(ctx.caller_id),
        joined_at=_NOW,
    )

    response = await ctx.client.delete(
        f"/conversations/{conversation_id}/participants/{ctx.caller_id}"
    )

    assert response.status_code == 204


async def test_removing_someone_else_without_owning_the_conversation_is_forbidden(
    ctx: _Context,
) -> None:
    other = uuid4()
    conversation_id = await _seed_conversation(ctx.conversations, owner_id=uuid4())
    await ctx.conversations.add_participant(
        conversation_id=conversation_id, participant_id=ParticipantId(other), joined_at=_NOW
    )

    response = await ctx.client.delete(f"/conversations/{conversation_id}/participants/{other}")

    assert response.status_code == 403


async def test_removing_the_owner_is_a_conflict(ctx: _Context) -> None:
    created = await ctx.client.post("/conversations", json={"title": "room"})
    conversation_id = created.json()["id"]

    response = await ctx.client.delete(
        f"/conversations/{conversation_id}/participants/{ctx.caller_id}"
    )

    assert response.status_code == 409


async def test_listing_participants_of_a_conversation_you_are_not_in_is_forbidden(
    ctx: _Context,
) -> None:
    conversation_id = await _seed_conversation(ctx.conversations, owner_id=uuid4())

    response = await ctx.client.get(f"/conversations/{conversation_id}/participants")

    assert response.status_code == 403


async def test_routes_require_authentication() -> None:
    conversations = FakeConversationRepository()
    messages = FakeMessageRepository()
    app = _build_app(conversations, messages, uuid4(), authenticated=False)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/conversations")

    assert response.status_code == 401
