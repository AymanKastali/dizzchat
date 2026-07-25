"""Unit tests for the RefreshToken aggregate — issue, activity window, revoke, rotate."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from dizzchat.contexts.identity.domain.refresh_token import InvalidRefreshToken, RefreshToken
from dizzchat.contexts.identity.domain.user import UserId

_NOW = datetime(2024, 1, 1, tzinfo=UTC)
_TTL = timedelta(days=1)


def _issue(jti: str = "jti-1") -> RefreshToken:
    return RefreshToken.issue(
        jti=jti,
        user_id=UserId(uuid4()),
        token_hash="hash",
        issued_at=_NOW,
        ttl=_TTL,
    )


def test_issue_sets_expiry_from_ttl_and_is_not_revoked() -> None:
    token = _issue()
    assert token.expires_at == _NOW + _TTL
    assert token.revoked_at is None


def test_active_when_not_revoked_and_not_expired() -> None:
    assert _issue().is_active(_NOW) is True


def test_inactive_when_expired() -> None:
    assert _issue().is_active(_NOW + timedelta(days=2)) is False


def test_inactive_when_revoked() -> None:
    token = _issue()
    token.revoke(_NOW)
    assert token.is_active(_NOW) is False


def test_revoke_is_idempotent() -> None:
    token = _issue()
    token.revoke(_NOW)
    first_revoked_at = token.revoked_at
    token.revoke(_NOW + timedelta(hours=1))
    assert token.revoked_at == first_revoked_at


def test_rotate_revokes_the_old_token_and_returns_an_active_successor() -> None:
    old = _issue("jti-old")
    new = old.rotate(now=_NOW, new_jti="jti-new", new_token_hash="hash2", ttl=_TTL)

    assert old.is_active(_NOW) is False
    assert new.is_active(_NOW) is True
    assert new.jti == "jti-new"
    assert new.user_id == old.user_id


def test_rotate_rejects_an_inactive_token() -> None:
    old = _issue()
    old.revoke(_NOW)
    with pytest.raises(InvalidRefreshToken):
        old.rotate(now=_NOW, new_jti="jti-new", new_token_hash="hash2", ttl=_TTL)
