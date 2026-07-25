"""RefreshToken aggregate root."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from dizzchat.contexts.identity.domain.refresh_token.errors import InvalidRefreshToken
from dizzchat.contexts.identity.domain.user.value_objects import UserId


@dataclass(eq=False, slots=True)
class RefreshToken:
    """A persisted refresh token, identified by its ``jti``.

    Owns its lifecycle — issued, checked for activity, revoked, and rotated. Separate from
    ``User`` (referenced by id), and stores only the *hash* of the token secret, never the secret.
    """

    jti: str
    user_id: UserId
    token_hash: str
    expires_at: datetime
    revoked_at: datetime | None = None

    @classmethod
    def issue(
        cls,
        *,
        jti: str,
        user_id: UserId,
        token_hash: str,
        issued_at: datetime,
        ttl: timedelta,
    ) -> RefreshToken:
        """Issue a fresh token that expires ``ttl`` after ``issued_at``."""
        return cls(jti=jti, user_id=user_id, token_hash=token_hash, expires_at=issued_at + ttl)

    def is_active(self, now: datetime) -> bool:
        """True when the token is neither revoked nor expired at ``now``."""
        return self.revoked_at is None and now < self.expires_at

    def revoke(self, now: datetime) -> None:
        """Mark the token revoked. Idempotent — re-revoking keeps the original timestamp."""
        if self.revoked_at is None:
            self.revoked_at = now

    def rotate(
        self,
        *,
        now: datetime,
        new_jti: str,
        new_token_hash: str,
        ttl: timedelta,
    ) -> RefreshToken:
        """Revoke this token and issue its successor; rejects rotating an inactive token."""
        if not self.is_active(now):
            raise InvalidRefreshToken()
        self.revoke(now)
        return RefreshToken.issue(
            jti=new_jti,
            user_id=self.user_id,
            token_hash=new_token_hash,
            issued_at=now,
            ttl=ttl,
        )

    def __eq__(self, other: object) -> bool:
        return isinstance(other, RefreshToken) and other.jti == self.jti

    def __hash__(self) -> int:
        return hash(self.jti)
