"""Shared test setup.

The connection strings have no code defaults (they are environment-specific), so provide
throwaway values here before the app is imported. The smoke tests never open these
connections; they only need ``Settings`` to construct.
"""

import os

os.environ.setdefault(
    "DATABASE_URL", "postgresql+asyncpg://dizzchat:dizzchat@localhost:5432/dizzchat"
)
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
