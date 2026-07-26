"""A ``UserDirectory`` adapter backed by the Identity context.

The anti-corruption layer between the two bounded contexts: Identity's ``Email`` value object and
``User`` aggregate are constructed and consumed *here*, and only a bare ``UUID`` crosses into
Messaging. Infrastructure is the right home for this — it keeps the cross-context dependency out of
Messaging's domain and use cases, which know nothing but the ``UserDirectory`` port.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from dizzchat.contexts.identity.domain.user import Email, InvalidEmail
from dizzchat.contexts.identity.infrastructure.outbound.persistence.repositories import (
    SqlAlchemyUserRepository,
)


class IdentityUserDirectory:
    """Implements the ``UserDirectory`` port by looking the user up in Identity's store."""

    def __init__(self, session: AsyncSession) -> None:
        self._users = SqlAlchemyUserRepository(session)

    async def find_id_by_email(self, email: str) -> UUID | None:
        try:
            address = Email(email)
        except InvalidEmail:
            # A malformed address is "no such user", not a server error: the caller turns the
            # ``None`` into a 404 exactly as it would for an unregistered address.
            return None
        user = await self._users.get_by_email(address)
        return user.id.value if user is not None else None
