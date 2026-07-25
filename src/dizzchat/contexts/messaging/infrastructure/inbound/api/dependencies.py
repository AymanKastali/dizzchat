"""FastAPI dependency wiring (the composition root for the Conversations context).

Builds the use-case handlers from a request-scoped DB session and the shared clock. The caller's
identity comes from the Identity context's ``get_current_user`` dependency, reused here.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from dizzchat.contexts.messaging.application.services import (
    CreateConversation,
    DeleteConversation,
    GetConversationHistory,
    ListConversations,
    RenameConversation,
)
from dizzchat.contexts.messaging.infrastructure.outbound.persistence import (
    SqlAlchemyConversationRepository,
    SqlAlchemyMessageRepository,
)
from dizzchat.shared.infrastructure.inbound.api.dependencies import ClockDep, SessionDep


def provide_create_conversation(session: SessionDep, clock: ClockDep) -> CreateConversation:
    return CreateConversation(SqlAlchemyConversationRepository(session), clock)


def provide_list_conversations(session: SessionDep) -> ListConversations:
    return ListConversations(SqlAlchemyConversationRepository(session))


def provide_rename_conversation(session: SessionDep, clock: ClockDep) -> RenameConversation:
    return RenameConversation(SqlAlchemyConversationRepository(session), clock)


def provide_delete_conversation(session: SessionDep, clock: ClockDep) -> DeleteConversation:
    return DeleteConversation(SqlAlchemyConversationRepository(session), clock)


def provide_get_conversation_history(session: SessionDep) -> GetConversationHistory:
    return GetConversationHistory(
        SqlAlchemyConversationRepository(session),
        SqlAlchemyMessageRepository(session),
    )


CreateConversationDep = Annotated[CreateConversation, Depends(provide_create_conversation)]
ListConversationsDep = Annotated[ListConversations, Depends(provide_list_conversations)]
RenameConversationDep = Annotated[RenameConversation, Depends(provide_rename_conversation)]
DeleteConversationDep = Annotated[DeleteConversation, Depends(provide_delete_conversation)]
GetConversationHistoryDep = Annotated[
    GetConversationHistory, Depends(provide_get_conversation_history)
]
