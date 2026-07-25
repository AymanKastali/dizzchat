"""Unit tests for the Conversation value objects."""

from uuid import uuid4

import pytest

from dizzchat.contexts.conversations.domain.conversation import (
    ConversationId,
    ConversationTitle,
    InvalidConversationTitle,
    OwnerId,
)


def test_title_is_trimmed() -> None:
    assert ConversationTitle("  Morning standup  ").value == "Morning standup"


def test_title_rejects_a_blank_value() -> None:
    with pytest.raises(InvalidConversationTitle):
        ConversationTitle("   ")


def test_title_rejects_an_over_long_value() -> None:
    with pytest.raises(InvalidConversationTitle):
        ConversationTitle("x" * 201)


def test_ids_are_equal_by_value() -> None:
    raw = uuid4()
    assert ConversationId(raw) == ConversationId(raw)
    assert OwnerId(raw) == OwnerId(raw)
