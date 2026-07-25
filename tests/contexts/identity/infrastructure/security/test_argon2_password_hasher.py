"""Tests for the argon2 password hasher adapter."""

from dizzchat.contexts.identity.infrastructure.outbound.security.argon2_password_hasher import (
    Argon2PasswordHasher,
)


def test_hash_is_not_the_plaintext() -> None:
    hasher = Argon2PasswordHasher()

    password_hash = hasher.hash("s3cret-pass")

    assert "s3cret-pass" not in password_hash.value
    assert password_hash.value.startswith("$argon2")


def test_verify_accepts_the_right_password_and_rejects_others() -> None:
    hasher = Argon2PasswordHasher()
    password_hash = hasher.hash("s3cret-pass")

    assert hasher.verify("s3cret-pass", password_hash) is True
    assert hasher.verify("wrong", password_hash) is False
