"""add conversation_participants so several users can share a conversation

Revision ID: 0005_conversation_participants
Revises: 0004_client_message_id
Create Date: 2026-07-26

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_conversation_participants"
down_revision: str | None = "0004_client_message_id"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "conversation_participants",
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("joined_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"]),
        # Composite PK: a user cannot be admitted to the same conversation twice.
        sa.PrimaryKeyConstraint("conversation_id", "user_id"),
    )
    op.create_index(
        "ix_conversation_participants_user_id", "conversation_participants", ["user_id"]
    )

    # Backfill, not optional: access is now decided by membership, so without a row per existing
    # conversation its own owner could no longer open a socket, post, or read its history. No WHERE
    # clause, so soft-deleted conversations are backfilled too and stay restorable.
    op.execute(
        """
        INSERT INTO conversation_participants (conversation_id, user_id, joined_at)
        SELECT id, owner_id, created_at FROM conversations
        """
    )


def downgrade() -> None:
    op.drop_index("ix_conversation_participants_user_id", table_name="conversation_participants")
    op.drop_table("conversation_participants")
