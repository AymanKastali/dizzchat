"""In-memory fakes for the Messaging ports, shared by the application and API tests."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

from dizzchat.contexts.messaging.domain.conversation import (
    Conversation,
    ConversationId,
    ConversationTitle,
    OwnerId,
    Participant,
    ParticipantId,
)
from dizzchat.contexts.messaging.domain.message import (
    ClientMessageId,
    Message,
    MessageContent,
    MessageId,
    MessageRole,
    SenderId,
)


class FakeConversationRepository:
    """In-memory ``ConversationRepository`` keyed by conversation id.

    ``get``/``list_for_participant`` return detached copies, so only ``update`` and the
    ``*_participant`` writes persist a change — mirroring the real adapter, which reconstructs
    domain objects from rows. Memberships are stored separately from the aggregate (as the real
    schema does) so ``joined_at`` can be served by ``list_participants``.
    """

    def __init__(self) -> None:
        self._by_id: dict[ConversationId, Conversation] = {}
        self._participants: dict[ConversationId, dict[ParticipantId, datetime]] = {}

    async def create(
        self,
        *,
        conversation_id: ConversationId,
        owner_id: OwnerId,
        title: ConversationTitle,
        created_at: datetime,
        updated_at: datetime,
        participant_ids: frozenset[ParticipantId],
    ) -> None:
        self._by_id[conversation_id] = Conversation(
            id=conversation_id,
            owner_id=owner_id,
            title=title,
            created_at=created_at,
            updated_at=updated_at,
        )
        self._participants[conversation_id] = dict.fromkeys(participant_ids, created_at)

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
        stored = await self.get_including_deleted(conversation_id)
        if stored is None or stored.is_deleted:
            return None
        return stored

    async def get_including_deleted(self, conversation_id: ConversationId) -> Conversation | None:
        stored = self._by_id.get(conversation_id)
        if stored is None:
            return None
        return replace(stored, participant_ids=frozenset(self._joined(conversation_id)))

    async def list_for_participant(self, participant_id: ParticipantId) -> list[Conversation]:
        active = [
            stored
            for stored in self._by_id.values()
            if participant_id in self._joined(stored.id) and not stored.is_deleted
        ]
        active.sort(key=lambda c: c.created_at, reverse=True)
        return [replace(c, participant_ids=frozenset(self._joined(c.id))) for c in active]

    async def add_participant(
        self,
        *,
        conversation_id: ConversationId,
        participant_id: ParticipantId,
        joined_at: datetime,
    ) -> None:
        self._participants.setdefault(conversation_id, {})[participant_id] = joined_at

    async def remove_participant(
        self, *, conversation_id: ConversationId, participant_id: ParticipantId
    ) -> None:
        self._joined(conversation_id).pop(participant_id, None)

    async def list_participants(self, conversation_id: ConversationId) -> list[Participant]:
        joined = sorted(self._joined(conversation_id).items(), key=lambda item: item[1])
        return [Participant(id=pid, joined_at=at) for pid, at in joined]

    def _joined(self, conversation_id: ConversationId) -> dict[ParticipantId, datetime]:
        return self._participants.setdefault(conversation_id, {})


class FakeUserDirectory:
    """In-memory ``UserDirectory`` mapping known email addresses to user ids."""

    def __init__(self, by_email: dict[str, UUID] | None = None) -> None:
        self._by_email = dict(by_email or {})

    def register(self, email: str, user_id: UUID) -> None:
        self._by_email[email.strip().lower()] = user_id

    async def find_id_by_email(self, email: str) -> UUID | None:
        return self._by_email.get(email.strip().lower())


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
        client_message_id: ClientMessageId | None = None,
    ) -> Message:
        message = Message(
            id=MessageId(self._next_id),
            conversation_id=conversation_id,
            sender_id=sender_id,
            role=role,
            content=content,
            created_at=created_at,
            client_message_id=client_message_id,
        )
        self._next_id += 1
        self._messages.append(message)
        return message

    async def find_by_client_message_id(
        self, conversation_id: ConversationId, client_message_id: ClientMessageId
    ) -> Message | None:
        for message in self._messages:
            if (
                message.conversation_id == conversation_id
                and message.client_message_id == client_message_id
            ):
                return message
        return None

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

    async def list_since(
        self,
        conversation_id: ConversationId,
        *,
        after: MessageId | None,
        limit: int,
    ) -> list[Message]:
        matches = [m for m in self._messages if m.conversation_id == conversation_id]
        if after is not None:
            matches = [m for m in matches if m.id.value > after.value]
        matches.sort(key=lambda m: m.id.value)
        return matches[:limit]


class FixedClock:
    """A ``Clock`` frozen at a fixed instant."""

    def __init__(self, now: datetime) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now


_FIXED_NOW = datetime(2024, 1, 1, tzinfo=UTC)


class FakeMessageWriter:
    """In-memory ``MessageWriter`` that records writes and assigns an incrementing id.

    ``from_user`` honours ``client_message_id`` idempotency (a repeat key returns the existing
    message with ``created=False``), mirroring the real adapter's dedupe.
    """

    def __init__(self) -> None:
        self.written: list[Message] = []
        self._next_id = 1
        self._by_client_id: dict[tuple[ConversationId, ClientMessageId], Message] = {}

    async def from_user(
        self,
        *,
        conversation_id: ConversationId,
        sender_id: SenderId,
        content: MessageContent,
        client_message_id: ClientMessageId | None = None,
    ) -> tuple[Message, bool]:
        if client_message_id is not None:
            existing = self._by_client_id.get((conversation_id, client_message_id))
            if existing is not None:
                return existing, False
        message = self._record(
            conversation_id, sender_id, MessageRole.USER, content, client_message_id
        )
        if client_message_id is not None:
            self._by_client_id[(conversation_id, client_message_id)] = message
        return message, True

    async def from_assistant(
        self, *, conversation_id: ConversationId, content: MessageContent
    ) -> Message:
        return self._record(conversation_id, None, MessageRole.ASSISTANT, content, None)

    def _record(
        self,
        conversation_id: ConversationId,
        sender_id: SenderId | None,
        role: MessageRole,
        content: MessageContent,
        client_message_id: ClientMessageId | None,
    ) -> Message:
        message = Message(
            id=MessageId(self._next_id),
            conversation_id=conversation_id,
            sender_id=sender_id,
            role=role,
            content=content,
            created_at=_FIXED_NOW,
            client_message_id=client_message_id,
        )
        self._next_id += 1
        self.written.append(message)
        return message


class FailingUserWriter(FakeMessageWriter):
    """A ``MessageWriter`` whose user write always fails, to exercise the DB-error path."""

    async def from_user(
        self,
        *,
        conversation_id: ConversationId,
        sender_id: SenderId,
        content: MessageContent,
        client_message_id: ClientMessageId | None = None,
    ) -> tuple[Message, bool]:
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


class NoOpSubscriber:
    """A ``ConversationSubscriber`` that does nothing, for route tests that run without Redis."""

    async def subscribe(self, conversation_id: ConversationId) -> None: ...

    async def unsubscribe(self, conversation_id: ConversationId) -> None: ...


class StubMessageReplayer:
    """A ``MessageReplayer`` that returns a preset list and records the cursor it was asked for."""

    def __init__(self, missed: list[Message] | None = None) -> None:
        self._missed = missed or []
        self.replayed_after: MessageId | None = None
        self.calls = 0

    async def replay_since(
        self, *, conversation_id: ConversationId, after: MessageId | None
    ) -> list[Message]:
        self.calls += 1
        self.replayed_after = after
        return list(self._missed)
