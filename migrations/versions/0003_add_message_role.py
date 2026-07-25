"""add message role and make sender nullable

Revision ID: 0003_message_role
Revises: 0002_conversations
Create Date: 2026-07-25

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_message_role"
down_revision: str | None = "0002_conversations"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    # Add the column with a server default so any existing rows backfill to 'user', then drop the
    # default so the application supplies the role explicitly on every insert.
    op.add_column(
        "messages",
        sa.Column("role", sa.String(length=16), nullable=False, server_default="user"),
    )
    op.alter_column("messages", "role", server_default=None)
    # An assistant message has no Identity user as its sender.
    op.alter_column("messages", "sender_id", existing_type=sa.Uuid(), nullable=True)


def downgrade() -> None:
    op.alter_column("messages", "sender_id", existing_type=sa.Uuid(), nullable=False)
    op.drop_column("messages", "role")
