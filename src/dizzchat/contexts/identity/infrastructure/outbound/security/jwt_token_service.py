"""JWT + opaque-refresh implementation of the ``TokenService`` port.

Access tokens are signed, short-lived JWTs. Refresh tokens are opaque ``<jti>.<secret>`` strings:
the high-entropy ``secret`` is returned to the client once, and only its SHA-256 hash is persisted,
so a database leak cannot reveal a usable token.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import jwt

from dizzchat.contexts.identity.application.dto import (
    AccessClaims,
    GeneratedRefreshToken,
)
from dizzchat.contexts.identity.application.errors import InvalidAccessToken
from dizzchat.contexts.identity.domain.refresh_token import InvalidRefreshToken
from dizzchat.contexts.identity.domain.user import UserId


class JwtTokenService:
    def __init__(self, secret_key: str, algorithm: str, access_ttl: timedelta) -> None:
        self._secret_key = secret_key
        self._algorithm = algorithm
        self._access_ttl = access_ttl

    def issue_access(self, user_id: UserId) -> str:
        now = datetime.now(UTC)
        payload = {
            "sub": str(user_id),
            "iat": int(now.timestamp()),
            "exp": int((now + self._access_ttl).timestamp()),
        }
        return jwt.encode(payload, self._secret_key, algorithm=self._algorithm)

    def decode_access(self, token: str) -> AccessClaims:
        try:
            payload = jwt.decode(token, self._secret_key, algorithms=[self._algorithm])
            return AccessClaims(user_id=UserId(UUID(payload["sub"])))
        except (jwt.PyJWTError, KeyError, ValueError):
            raise InvalidAccessToken() from None

    def generate_refresh(self) -> GeneratedRefreshToken:
        jti = uuid4().hex
        secret = secrets.token_urlsafe(32)
        return GeneratedRefreshToken(
            jti=jti,
            token=f"{jti}.{secret}",
            token_hash=self._hash_secret(secret),
        )

    def parse_refresh(self, token: str) -> tuple[str, str]:
        jti, separator, secret = token.partition(".")
        if not separator or not jti or not secret:
            raise InvalidRefreshToken()
        return jti, secret

    def verify_refresh(self, secret: str, token_hash: str) -> bool:
        return hmac.compare_digest(self._hash_secret(secret), token_hash)

    def _hash_secret(self, secret: str) -> str:
        return hashlib.sha256(secret.encode()).hexdigest()
