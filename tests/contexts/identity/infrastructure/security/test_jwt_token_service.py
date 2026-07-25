"""Tests for the JWT + opaque-refresh token service adapter."""

from datetime import timedelta
from uuid import uuid4

import pytest

from dizzchat.contexts.identity.application.errors import InvalidAccessToken
from dizzchat.contexts.identity.domain.refresh_token import InvalidRefreshToken
from dizzchat.contexts.identity.domain.user import UserId
from dizzchat.contexts.identity.infrastructure.outbound.security.jwt_token_service import (
    JwtTokenService,
)


def _service(access_ttl: timedelta = timedelta(minutes=15)) -> JwtTokenService:
    secret_key = "test-secret-key-that-is-at-least-32-bytes-long"
    return JwtTokenService(secret_key=secret_key, algorithm="HS256", access_ttl=access_ttl)


def test_access_token_round_trips_the_user_id() -> None:
    service = _service()
    user_id = UserId(uuid4())

    claims = service.decode_access(service.issue_access(user_id))

    assert claims.user_id == user_id


def test_decode_rejects_a_tampered_token() -> None:
    service = _service()
    token = service.issue_access(UserId(uuid4()))

    with pytest.raises(InvalidAccessToken):
        service.decode_access(token + "tampered")


def test_decode_rejects_an_expired_token() -> None:
    service = _service(access_ttl=timedelta(seconds=-1))

    with pytest.raises(InvalidAccessToken):
        service.decode_access(service.issue_access(UserId(uuid4())))


def test_refresh_secret_round_trips_through_verify() -> None:
    service = _service()
    generated = service.generate_refresh()

    jti, secret = service.parse_refresh(generated.token)

    assert jti == generated.jti
    assert service.verify_refresh(secret, generated.token_hash) is True
    assert service.verify_refresh("wrong-secret", generated.token_hash) is False


def test_parse_rejects_a_malformed_refresh_token() -> None:
    with pytest.raises(InvalidRefreshToken):
        _service().parse_refresh("no-dot-here")
