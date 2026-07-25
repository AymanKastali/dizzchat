"""Codec for carrying a domain ``Message`` over Redis pub/sub as JSON bytes.

The field layout mirrors the outbound WebSocket payload (see ``realtime.protocol._message_payload``)
so the two stay in step, but this is the transport format for cross-replica fan-out — a subscriber
decodes it back into a domain ``Message`` and hands that to local delivery.
"""

from __future__ import annotations

import json
from datetime import datetime
from uuid import UUID

from dizzchat.contexts.messaging.domain.conversation import ConversationId
from dizzchat.contexts.messaging.domain.message import (
    Message,
    MessageContent,
    MessageId,
    MessageRole,
    SenderId,
)


def encode(message: Message) -> bytes:
    """Serialize a message to the JSON bytes published on its conversation channel."""
    payload = {
        "id": message.id.value,
        "conversation_id": str(message.conversation_id.value),
        "sender_id": str(message.sender_id.value) if message.sender_id is not None else None,
        "role": message.role.value,
        "content": message.content.value,
        "created_at": message.created_at.isoformat(),
    }
    return json.dumps(payload).encode("utf-8")


def decode(data: bytes) -> Message:
    """Reconstruct a domain ``Message`` from the JSON bytes received on a conversation channel."""
    raw = json.loads(data)
    sender_id = raw["sender_id"]
    return Message(
        id=MessageId(raw["id"]),
        conversation_id=ConversationId(UUID(raw["conversation_id"])),
        sender_id=SenderId(UUID(sender_id)) if sender_id is not None else None,
        role=MessageRole(raw["role"]),
        content=MessageContent(raw["content"]),
        created_at=datetime.fromisoformat(raw["created_at"]),
    )
