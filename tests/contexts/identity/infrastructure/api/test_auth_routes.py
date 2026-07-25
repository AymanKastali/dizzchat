"""End-to-end API tests for the auth routes, wired to in-memory fakes (no database)."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient

from dizzchat.app import create_app
from dizzchat.contexts.identity.application.services import (
    AuthenticateUser,
    RefreshAccessToken,
    RegisterUser,
)
from dizzchat.contexts.identity.infrastructure.inbound.api.dependencies import (
    get_token_service,
    provide_authenticate_user,
    provide_refresh_access_token,
    provide_register_user,
)
from tests.contexts.identity.fakes import (
    FakeHasher,
    FakeRefreshTokenRepository,
    FakeTokenService,
    FakeUserRepository,
    FixedClock,
)

_NOW = datetime(2024, 1, 1, tzinfo=UTC)
_TTL = timedelta(days=14)


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    """A client whose auth handlers are wired to shared in-memory fakes."""
    users = FakeUserRepository()
    refresh_tokens = FakeRefreshTokenRepository()
    hasher = FakeHasher()
    clock = FixedClock(_NOW)
    tokens = FakeTokenService()

    app = create_app()
    app.dependency_overrides[provide_register_user] = lambda: RegisterUser(users, hasher, clock)
    app.dependency_overrides[provide_authenticate_user] = lambda: AuthenticateUser(
        users, refresh_tokens, hasher, tokens, clock, _TTL
    )
    app.dependency_overrides[provide_refresh_access_token] = lambda: RefreshAccessToken(
        refresh_tokens, tokens, clock, _TTL
    )
    app.dependency_overrides[get_token_service] = lambda: tokens

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http_client:
        yield http_client


async def test_signup_creates_a_user(client: AsyncClient) -> None:
    response = await client.post(
        "/auth/signup", json={"email": "User@Example.com", "password": "password"}
    )

    assert response.status_code == 201
    body = response.json()
    assert body["email"] == "user@example.com"
    assert "id" in body


async def test_signup_rejects_a_duplicate_email(client: AsyncClient) -> None:
    payload = {"email": "user@example.com", "password": "password"}
    await client.post("/auth/signup", json=payload)

    response = await client.post("/auth/signup", json=payload)

    assert response.status_code == 409


async def test_signup_rejects_an_invalid_email(client: AsyncClient) -> None:
    response = await client.post(
        "/auth/signup", json={"email": "not-an-email", "password": "password"}
    )

    assert response.status_code == 422


async def test_signup_then_login_then_access_a_protected_route(client: AsyncClient) -> None:
    await client.post("/auth/signup", json={"email": "user@example.com", "password": "password"})

    login = await client.post(
        "/auth/login", json={"email": "user@example.com", "password": "password"}
    )
    assert login.status_code == 200
    access_token = login.json()["access_token"]

    me = await client.get("/auth/me", headers={"Authorization": f"Bearer {access_token}"})
    assert me.status_code == 200
    assert "user_id" in me.json()


async def test_login_rejects_a_wrong_password(client: AsyncClient) -> None:
    await client.post("/auth/signup", json={"email": "user@example.com", "password": "password"})

    response = await client.post(
        "/auth/login", json={"email": "user@example.com", "password": "wrong"}
    )

    assert response.status_code == 401


async def test_protected_route_requires_a_token(client: AsyncClient) -> None:
    response = await client.get("/auth/me")

    assert response.status_code == 401


async def test_refresh_rotates_and_rejects_reuse(client: AsyncClient) -> None:
    await client.post("/auth/signup", json={"email": "user@example.com", "password": "password"})
    login = await client.post(
        "/auth/login", json={"email": "user@example.com", "password": "password"}
    )
    old_refresh = login.json()["refresh_token"]

    rotated = await client.post("/auth/refresh", json={"refresh_token": old_refresh})
    assert rotated.status_code == 200
    assert rotated.json()["refresh_token"] != old_refresh

    reused = await client.post("/auth/refresh", json={"refresh_token": old_refresh})
    assert reused.status_code == 401
