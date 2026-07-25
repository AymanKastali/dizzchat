"""Application configuration, loaded from the environment via pydantic-settings."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Process-wide settings. Values come from the environment or a local ``.env``."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    environment: str = "development"
    log_level: str = "INFO"

    host: str = "0.0.0.0"
    port: int = 8000

    database_url: str
    redis_url: str

    # Signing key for access-token JWTs — required, no default, and must be kept secret.
    # HS256 needs at least 32 bytes of key material; enforce it so a weak key fails fast.
    jwt_secret_key: str = Field(min_length=32)
    jwt_algorithm: str = "HS256"
    access_token_ttl_seconds: int = 900  # 15 minutes — short-lived, per the brief.
    refresh_token_ttl_seconds: int = 1_209_600  # 14 days.

    # How long a freshly-opened WebSocket has to send its auth frame before it is closed (4401).
    ws_auth_timeout_seconds: float = 5.0

    # JSON array in the environment, e.g. CORS_ALLOW_ORIGINS=["https://app.example.com"].
    # Empty by default: cross-origin access is opt-in per environment, never wildcard-by-default.
    cors_allow_origins: list[str] = Field(default_factory=list)


@lru_cache
def get_settings() -> Settings:
    """Return the cached settings instance."""
    # pydantic-settings populates required fields from the environment at runtime; Pyright
    # can't see that, so it wrongly flags the missing arguments (mypy's plugin understands it).
    return Settings()  # pyright: ignore[reportCallIssue]
