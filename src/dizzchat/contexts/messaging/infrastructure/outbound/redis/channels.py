"""Redis pub/sub channel naming: one channel per conversation."""

from __future__ import annotations

from dizzchat.contexts.messaging.domain.conversation import ConversationId


def conversation_channel(conversation_id: ConversationId) -> str:
    """The pub/sub channel a conversation's messages fan out on."""
    return f"conv:{conversation_id.value}"
