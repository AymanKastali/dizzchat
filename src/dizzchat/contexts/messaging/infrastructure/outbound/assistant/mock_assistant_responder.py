"""A mock ``AssistantResponder`` — the stand-in AI for this backend-focused project."""

from __future__ import annotations

from dizzchat.contexts.messaging.domain.message import MessageContent


class MockAssistantResponder:
    """Echoes the user's message back as the assistant reply.

    The assignment evaluates the backend, not a model, so the "AI" is a canned echo. It is async
    and does no I/O, so it never blocks the event loop; a real responder would implement the same
    ``AssistantResponder`` port.
    """

    async def reply_to(self, prompt: MessageContent) -> MessageContent:
        return MessageContent(f"You said: {prompt.value}")
