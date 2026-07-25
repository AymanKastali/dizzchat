"""Apply Alembic migrations from within the application process.

Running ``alembic upgrade head`` on startup keeps deploys migration-free: every replica
self-migrates on boot, serialized by the advisory lock in ``migrations/env.py`` so only one
actually applies the schema and the rest no-op. Reuses the async ``env.py`` (which reads
``DATABASE_URL`` from ``Settings``), so no engine wiring is duplicated here.
"""

from pathlib import Path

from alembic import command
from alembic.config import Config


def _project_root() -> Path:
    """Return the directory holding ``alembic.ini`` (and the ``migrations/`` package)."""
    for directory in Path(__file__).resolve().parents:
        if (directory / "alembic.ini").is_file():
            return directory
    raise RuntimeError("alembic.ini not found in any parent directory")


def run_migrations() -> None:
    """Upgrade the database to the latest revision.

    Synchronous and blocking, and it opens its own event loop via ``env.py``; call it off the
    running event loop (e.g. ``asyncio.to_thread``), never inside one.
    """
    root = _project_root()
    # Build the config programmatically instead of from alembic.ini on purpose: passing the
    # ini path makes env.py call logging.fileConfig(), which would reset the app's structured
    # logging mid-startup. env.py reads the DB URL from Settings, so only script_location is
    # needed here; the ini stays the source of truth for the Alembic CLI.
    config = Config()
    # Absolute, so resolution never depends on the process's working directory.
    config.set_main_option("script_location", str(root / "migrations"))
    command.upgrade(config, "head")
