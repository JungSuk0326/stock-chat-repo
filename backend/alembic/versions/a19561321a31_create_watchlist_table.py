"""create watchlist table

Revision ID: a19561321a31
Revises: dbe6c941c4e7
Create Date: 2026-05-25 17:59:16.784808

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a19561321a31'
down_revision: Union[str, Sequence[str], None] = 'dbe6c941c4e7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'watchlist',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('instrument_id', sa.BigInteger(), nullable=False),
        sa.Column('position', sa.Integer(), nullable=False),
        sa.Column(
            'added_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(['instrument_id'], ['instruments.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('instrument_id'),
    )
    # NOTE: Alembic autogenerate also tried to drop `prices_time_idx`. That index
    # is auto-created by TimescaleDB's create_hypertable() and is not represented
    # in our ORM models. The drop was removed manually and the index was restored
    # via raw SQL outside this migration (CREATE INDEX prices_time_idx ...).


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('watchlist')
