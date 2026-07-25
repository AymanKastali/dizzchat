"""Smoke test proving the app boots and the liveness endpoint responds."""

from httpx import ASGITransport, AsyncClient

from dizzchat.app import create_app


async def test_health_returns_ok() -> None:
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
