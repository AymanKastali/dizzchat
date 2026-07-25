"""In-memory fakes for the Messaging ports, shared by the application and API tests."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

from dizzchat.contexts.messaging.domain.conversation import (
    Conversation,
    ConversationId,
    ConversationTitle,
    OwnerId,
)
from dizzchat.contexts.messaging.domain.message import (
    Message,
    MessageContent,
    MessageId,
    MessageRole,
    SenderId,
)


class FakeConversationRepository:
    """In-memory ``ConversationRepository`` keyed by conversation id.

    ``get``/``list_for_owner`` return detached copies, so only ``update`` persists a change —
    mirroring the real adapter, which reconstructs domain objects from rows.
    """

    def __init__(self) -> None:
        self._by_id: dict[ConversationId, Conversation] = {}

    async def create(
        self,
        *,
        conversation_id: ConversationId,
        owner_id: OwnerId,
        title: ConversationTitle,
        created_at: datetime,
        updated_at: datetime,
    ) -> None:
        self._by_id[conversation_id] = Conversation(
            id=conversation_id,
            owner_id=owner_id,
            title=title,
            created_at=created_at,
            updated_at=updated_at,
        )

    async def update(
        self,
        *,
        conversation_id: ConversationId,
        title: ConversationTitle,
        updated_at: datetime,
        deleted_at: datetime | None,
    ) -> None:
        stored = self._by_id[conversation_id]
        stored.title = title
        stored.updated_at = updated_at
        stored.deleted_at = deleted_at

    async def get(self, conversation_id: ConversationId) -> Conversation | None:
        stored = self._by_id.get(conversation_id)
        if stored is None or stored.is_deleted:
            return None
        return replace(stored)

    async def list_for_owner(self, owner_id: OwnerId) -> list[Conversation]:
        active = [
            stored
            for stored in self._by_id.values()
            if stored.owner_id == owner_id and not stored.is_deleted
        ]
        active.sort(key=lambda c: c.created_at, reverse=True)
        return [replace(c) for c in active]


class FakeMessageRepository:
    """In-memory ``MessageRepository`` that assigns an incrementing id (the ordering seq)."""

    def __init__(self) -> None:
        self._messages: list[Message] = []
        self._next_id = 1

    async def create(
        self,
        *,
        conversation_id: ConversationId,
        sender_id: SenderId | None,
        role: MessageRole,
        content: MessageContent,
        created_at: datetime,
    ) -> Message:
        message = Message(
            id=MessageId(self._next_id),
            conversation_id=conversation_id,
            sender_id=sender_id,
            role=role,
            content=content,
            created_at=created_at,
        )
        self._next_id += 1
        self._messages.append(message)
        return message

    async def list_history(
        self,
        conversation_id: ConversationId,
        *,
        before: MessageId | None,
        limit: int,
    ) -> list[Message]:
        matches = [m for m in self._messages if m.conversation_id == conversation_id]
        if before is not None:
            matches = [m for m in matches if m.id.value < before.value]
        matches.sort(key=lambda m: m.id.value, reverse=True)
        return matches[:limit]


class FixedClock:
    """A ``Clock`` frozen at a fixed instant."""

    def __init__(self, now: datetime) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now


_FIXED_NOW = datetime(2024, 1, 1, tzinfo=UTC)


class FakeMessageWriter:
    """In-memory ``MessageWriter`` that records writes and assigns an incrementing id."""

    def __init__(self) -> None:
        self.written: list[Message] = []
        self._next_id = 1

    async def from_user(
        self, *, conversation_id: ConversationId, sender_id: SenderId, content: MessageContent
    ) -> Message:
        return self._record(conversation_id, sender_id, MessageRole.USER, content)

    async def from_assistant(
        self, *, conversation_id: ConversationId, content: MessageContent
    ) -> Message:
        return self._record(conversation_id, None, MessageRole.ASSISTANT, content)

    def _record(
        self,
        conversation_id: ConversationId,
        sender_id: SenderId | None,
        role: MessageRole,
        content: MessageContent,
    ) -> Message:
        message = Message(
            id=MessageId(self._next_id),
            conversation_id=conversation_id,
            sender_id=sender_id,
            role=role,
            content=content,
            created_at=_FIXED_NOW,
        )
        self._next_id += 1
        self.written.append(message)
        return message


class FailingUserWriter(FakeMessageWriter):
    """A ``MessageWriter`` whose user write always fails, to exercise the DB-error path."""

    async def from_user(
        self, *, conversation_id: ConversationId, sender_id: SenderId, content: MessageContent
    ) -> Message:
        raise RuntimeError("database unavailable")


class CannedAssistantResponder:
    """An ``AssistantResponder`` that always returns the same reply."""

    def __init__(self, reply: str = "canned reply") -> None:
        self._reply = reply

    async def reply_to(self, prompt: MessageContent) -> MessageContent:
        return MessageContent(self._reply)


class FailingAssistantResponder:
    """An ``AssistantResponder`` that always fails, to exercise the AI-error path."""

    async def reply_to(self, prompt: MessageContent) -> MessageContent:
        raise RuntimeError("assistant unavailable")


class RecordingBroadcaster:
    """A ``MessageBroadcaster`` that records every broadcast for assertions."""

    def __init__(self) -> None:
        self.broadcasts: list[tuple[ConversationId, Message]] = []

    async def broadcast(self, conversation_id: ConversationId, message: Message) -> None:
        self.broadcasts.append((conversation_id, message))
