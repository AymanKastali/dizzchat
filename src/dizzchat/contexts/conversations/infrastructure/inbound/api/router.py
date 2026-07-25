"""Wires the Conversations controllers onto their routes.

Registration only — the request-handling logic lives in ``controllers/``. Response models are
inferred from each controller's return annotation.
"""

from __future__ import annotations

from fastapi import APIRouter, status

from dizzchat.contexts.conversations.infrastructure.inbound.api.controllers import (
    create_conversation,
    delete_conversation,
    get_conversation_history,
    list_conversations,
    rename_conversation,
)

router = APIRouter(prefix="/conversations", tags=["conversations"])
router.add_api_route("", create_conversation, methods=["POST"], status_code=status.HTTP_201_CREATED)
router.add_api_route("", list_conversations, methods=["GET"])
router.add_api_route("/{conversation_id}", rename_conversation, methods=["PATCH"])
router.add_api_route(
    "/{conversation_id}",
    delete_conversation,
    methods=["DELETE"],
    status_code=status.HTTP_204_NO_CONTENT,
)
router.add_api_route("/{conversation_id}/messages", get_conversation_history, methods=["GET"])
