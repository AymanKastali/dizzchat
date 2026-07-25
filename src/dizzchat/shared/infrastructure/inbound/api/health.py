"""Liveness endpoint used by orchestration and the compose healthcheck."""

from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, str]:
    """Return a simple liveness signal."""
    return {"status": "ok"}
