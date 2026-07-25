"""In-memory fakes for the Identity ports, shared by the application and API tests."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from dizzchat.contexts.identity.application.dto import AccessClaims, GeneratedRefreshToken
from dizzchat.contexts.identity.application.errors import InvalidAccessToken
from dizzchat.contexts.identity.domain.refresh_token import InvalidRefreshToken, RefreshToken
from dizzchat.contexts.identity.domain.user import Email, PasswordHash, User, UserId


class FakeUserRepository:
    """In-memory ``UserRepository`` keyed by email."""

    def __init__(self) -> None:
        self._by_email: dict[str, User] = {}

    async def add(self, user: User) -> None:
        self._by_email[user.email.value] = user

    async def get_by_email(self, email: Email) -> User | None:
        return self._by_email.get(email.value)


class FakeRefreshTokenRepository:
    """In-memory ``RefreshTokenRepository`` keyed by ``jti``."""

    def __init__(self) -> None:
        self._by_jti: dict[str, RefreshToken] = {}

    async def add(self, token: RefreshToken) -> None:
        self._by_jti[token.jti] = token

    async def get_by_jti(self, jti: str) -> RefreshToken | None:
        return self._by_jti.get(jti)

    async def save(self, token: RefreshToken) -> None:
        self._by_jti[token.jti] = token


class FakeHasher:
    """Reversible ``PasswordHasher`` stand-in (``hashed:<plaintext>``) so tests need no crypto."""

    def hash(self, plaintext_password: str) -> PasswordHash:
        return PasswordHash(f"hashed:{plaintext_password}")

    def verify(self, candidate: str, password_hash: PasswordHash) -> bool:
        return password_hash.value == f"hashed:{candidate}"


class FakeTokenService:
    """Deterministic ``TokenService``: access ``access:<uuid>``, refresh ``<jti>.<secret>``."""

    _ACCESS_PREFIX = "access:"

    def __init__(self) -> None:
        self._counter = 0

    def issue_access(self, user_id: UserId) -> str:
        return f"{self._ACCESS_PREFIX}{user_id}"

    def decode_access(self, token: str) -> AccessClaims:
        if not token.startswith(self._ACCESS_PREFIX):
            raise InvalidAccessToken()
        try:
            return AccessClaims(user_id=UserId(UUID(token.removeprefix(self._ACCESS_PREFIX))))
        except ValueError:
            raise InvalidAccessToken() from None

    def generate_refresh(self) -> GeneratedRefreshToken:
        self._counter += 1
        jti, secret = f"jti-{self._counter}", f"secret-{self._counter}"
        return GeneratedRefreshToken(jti=jti, token=f"{jti}.{secret}", token_hash=f"hash:{secret}")

    def parse_refresh(self, token: str) -> tuple[str, str]:
        jti, separator, secret = token.partition(".")
        if not separator:
            raise InvalidRefreshToken()
        return jti, secret

    def verify_refresh(self, secret: str, token_hash: str) -> bool:
        return token_hash == f"hash:{secret}"


class FixedClock:
    """A ``Clock`` frozen at a fixed instant."""

    def __init__(self, now: datetime) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now
