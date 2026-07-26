"""Wires the Conversations controllers onto their routes.

Registration only — the request-handling logic lives in ``controllers/``. Response models are
inferred from each controller's return annotation.
"""

from __future__ import annotations

from fastapi import APIRouter, status

from dizzchat.contexts.messaging.infrastructure.inbound.api.controllers import (
    add_participant,
    create_conversation,
    delete_conversation,
    get_conversation_history,
    list_conversations,
    list_participants,
    remove_participant,
    rename_conversation,
    restore_conversation,
)
from dizzchat.shared.infrastructure.inbound.api.transactional_route import TransactionalRoute

# ``route_class`` makes the request the transaction boundary for every route registered below, so a
# write is durable before the client is told it happened.
router = APIRouter(prefix="/conversations", tags=["conversations"], route_class=TransactionalRoute)
router.add_api_route("", create_conversation, methods=["POST"], status_code=status.HTTP_201_CREATED)
router.add_api_route("", list_conversations, methods=["GET"])
router.add_api_route("/{conversation_id}", rename_conversation, methods=["PATCH"])
router.add_api_route(
    "/{conversation_id}",
    delete_conversation,
    methods=["DELETE"],
    status_code=status.HTTP_204_NO_CONTENT,
)
router.add_api_route("/{conversation_id}/restore", restore_conversation, methods=["POST"])
router.add_api_route("/{conversation_id}/messages", get_conversation_history, methods=["GET"])
router.add_api_route(
    "/{conversation_id}/participants",
    add_participant,
    methods=["POST"],
    status_code=status.HTTP_204_NO_CONTENT,
)
router.add_api_route("/{conversation_id}/participants", list_participants, methods=["GET"])
router.add_api_route(
    "/{conversation_id}/participants/{user_id}",
    remove_participant,
    methods=["DELETE"],
    status_code=status.HTTP_204_NO_CONTENT,
)
