"""Unit tests for the User aggregate's value objects."""

from uuid import uuid4

import pytest

from dizzchat.contexts.identity.domain.user import Email, InvalidEmail, PasswordHash, UserId


def test_email_normalizes_case_and_whitespace() -> None:
    assert Email("  Alice@Example.COM ").value == "alice@example.com"


def test_emails_equal_by_value() -> None:
    assert Email("a@b.com") == Email("A@B.com")


@pytest.mark.parametrize("bad", ["", "no-at", "a@b", "@b.com", "a@.com", "a b@c.com"])
def test_email_rejects_malformed(bad: str) -> None:
    with pytest.raises(InvalidEmail):
        Email(bad)


def test_password_hash_rejects_empty() -> None:
    with pytest.raises(ValueError):
        PasswordHash("")


def test_password_hash_stringifies_to_its_value() -> None:
    assert str(PasswordHash("$argon2id$abc")) == "$argon2id$abc"


def test_user_ids_equal_by_value() -> None:
    raw = uuid4()
    assert UserId(raw) == UserId(raw)
