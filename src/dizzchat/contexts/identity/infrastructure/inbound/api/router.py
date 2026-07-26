"""Wires the Identity controllers onto their routes.

Registration only — the request-handling logic lives in ``controllers/``. Response models are
inferred from each controller's return annotation.
"""

from __future__ import annotations

from fastapi import APIRouter, status

from dizzchat.contexts.identity.infrastructure.inbound.api.controllers import (
    current_user,
    login,
    refresh,
    signup,
)
from dizzchat.shared.infrastructure.inbound.api.transactional_route import TransactionalRoute

# ``route_class`` makes the request the transaction boundary for every route registered below, so a
# write is durable before the client is told it happened.
router = APIRouter(prefix="/auth", tags=["auth"], route_class=TransactionalRoute)
router.add_api_route("/signup", signup, methods=["POST"], status_code=status.HTTP_201_CREATED)
router.add_api_route("/login", login, methods=["POST"])
router.add_api_route("/refresh", refresh, methods=["POST"])
router.add_api_route("/me", current_user, methods=["GET"])
