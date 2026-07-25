"""add client_message_id with a per-conversation unique constraint

Revision ID: 0004_client_message_id
Revises: 0003_message_role
Create Date: 2026-07-25

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_client_message_id"
down_revision: str | None = "0003_message_role"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    # Nullable add + unique constraint: no backfill, and Postgres treats NULLs as distinct, so
    # existing rows (all NULL) and future keyless sends never collide.
    op.add_column("messages", sa.Column("client_message_id", sa.Uuid(), nullable=True))
    # This builds the backing unique index under ACCESS EXCLUSIVE, briefly blocking reads/writes on
    # `messages`. Acceptable here: migrations run transactionally under a pg_advisory_xact_lock (see
    # migrations/env.py) that serializes concurrent replica boots, and CREATE UNIQUE INDEX
    # CONCURRENTLY cannot run inside that transaction. On a large live table, build the index
    # CONCURRENTLY out-of-band, then ADD CONSTRAINT ... USING INDEX (see NOTES).
    op.create_unique_constraint(
        "uq_messages_conversation_id_client_message_id",
        "messages",
        ["conversation_id", "client_message_id"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_messages_conversation_id_client_message_id", "messages", type_="unique")
    op.drop_column("messages", "client_message_id")
