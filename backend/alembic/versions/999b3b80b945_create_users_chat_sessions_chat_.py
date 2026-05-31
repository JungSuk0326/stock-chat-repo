"""create users + chat_sessions + chat_messages tables

Revision ID: 999b3b80b945
Revises: 40eb797bc4b8
Create Date: 2026-05-31 21:18:41.340030

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '999b3b80b945'
down_revision: Union[str, Sequence[str], None] = '40eb797bc4b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "users",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    # Bootstrap the single-user "owner" row. App code references this
    # by OWNER_USER_ID = 1 (app/models/user.py). Wrapped in OVERRIDING
    # SYSTEM VALUE so the sequence cursor still advances correctly even
    # though we're forcing id=1.
    op.execute(
        "INSERT INTO users (id, name) VALUES (1, 'owner') "
        "ON CONFLICT DO NOTHING"
    )
    op.execute("SELECT setval('users_id_seq', GREATEST((SELECT MAX(id) FROM users), 1))")

    op.create_table(
        "chat_sessions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("instrument_id", sa.BigInteger(), nullable=False),
        sa.Column("title", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["instrument_id"], ["instruments.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_chat_sessions_user_instrument_updated",
        "chat_sessions",
        ["user_id", "instrument_id", "updated_at"],
        unique=False,
    )

    op.create_table(
        "chat_messages",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("session_id", sa.BigInteger(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("model", sa.String(length=64), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["session_id"], ["chat_sessions.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_chat_messages_session_created",
        "chat_messages",
        ["session_id", "created_at"],
        unique=False,
    )
    # NOTE: Alembic autogenerate tried to drop `prices_time_idx`. That index
    # is auto-created by TimescaleDB's create_hypertable() and is not in
    # our ORM. Drop removed manually (same workaround as the two prior
    # migrations).


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_chat_messages_session_created", table_name="chat_messages")
    op.drop_table("chat_messages")
    op.drop_index(
        "ix_chat_sessions_user_instrument_updated", table_name="chat_sessions"
    )
    op.drop_table("chat_sessions")
    op.drop_table("users")
