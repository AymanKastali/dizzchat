"""Unit tests for the User aggregate root — registration, password checks, identity equality."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from dizzchat.contexts.identity.domain.user import Email, PasswordHash, User, UserId
from tests.contexts.identity.credentials import PLAINTEXT_PW

_CREATED_AT = datetime(2024, 1, 1, tzinfo=UTC)


class _FakeHasher:
    """A reversible stand-in for a real hasher, so the domain test needs no crypto."""

    def hash(self, plaintext_password: str) -> PasswordHash:
        return PasswordHash(f"hashed:{plaintext_password}")

    def verify(self, candidate: str, password_hash: PasswordHash) -> bool:
        return password_hash.value == f"hashed:{candidate}"


def _user(raw_id: UUID, email: str = "user@example.com") -> User:
    return User(
        id=UserId(raw_id),
        email=Email(email),
        password_hash=PasswordHash("hash"),
        created_at=_CREATED_AT,
    )


def test_register_stores_a_hash_never_the_plaintext() -> None:
    user = User.register(
        user_id=UserId(uuid4()),
        email=Email("user@example.com"),
        plaintext_password=PLAINTEXT_PW,
        hasher=_FakeHasher(),
        created_at=_CREATED_AT,
    )

    # register delegates hashing to the injected hasher — plaintext never reaches a field.
    # (That the real hash excludes the plaintext is asserted in the argon2 adapter's own test.)
    assert user.password_hash == PasswordHash(f"hashed:{PLAINTEXT_PW}")


def test_verify_password_accepts_the_right_password_and_rejects_others() -> None:
    hasher = _FakeHasher()
    user = User.register(
        user_id=UserId(uuid4()),
        email=Email("user@example.com"),
        plaintext_password=PLAINTEXT_PW,
        hasher=hasher,
        created_at=_CREATED_AT,
    )

    assert user.verify_password(PLAINTEXT_PW, hasher) is True
    assert user.verify_password("wrong", hasher) is False


def test_users_are_equal_by_identity_not_fields() -> None:
    raw_id = uuid4()
    one = _user(raw_id, "one@example.com")
    other = _user(raw_id, "other@example.com")

    assert one == other
    assert hash(one) == hash(other)


def test_users_with_different_ids_are_not_equal() -> None:
    assert _user(uuid4()) != _user(uuid4())
