"""The read-side view of a conversation membership."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from dizzchat.contexts.messaging.domain.conversation.value_objects import ParticipantId


@dataclass(frozen=True, eq=True, slots=True)
class Participant:
    """A user's membership of a conversation, with the moment they joined.

    Read-only detail: the ``Conversation`` aggregate itself holds participant *ids* only, because
    identity is all it needs to enforce access. ``joined_at`` carries no invariant, so it is served
    from this projection rather than loaded into the aggregate.
    """

    id: ParticipantId
    joined_at: datetime
