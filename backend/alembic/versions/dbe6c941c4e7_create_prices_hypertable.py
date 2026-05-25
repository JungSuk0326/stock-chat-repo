"""create prices hypertable

Revision ID: dbe6c941c4e7
Revises: cc53dfe03fb8
Create Date: 2026-05-25 11:21:32.525901

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'dbe6c941c4e7'
down_revision: Union[str, Sequence[str], None] = 'cc53dfe03fb8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'prices',
        sa.Column('instrument_id', sa.BigInteger(), nullable=False),
        sa.Column('interval', sa.String(length=8), nullable=False),
        sa.Column('time', sa.DateTime(timezone=True), nullable=False),
        sa.Column('open', sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column('high', sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column('low', sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column('close', sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column('volume', sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(['instrument_id'], ['instruments.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('instrument_id', 'interval', 'time', name='pk_prices'),
    )

    # Convert to TimescaleDB hypertable. Alembic has no native op for this.
    # chunk_time_interval = 7 days suits both daily (1d) and intraday (1m/1h) bars
    # for KR + US use cases.
    op.execute(
        "SELECT create_hypertable('prices', 'time', "
        "chunk_time_interval => INTERVAL '7 days');"
    )

    # Enable native compression. Compress chunks older than 30 days.
    # `segmentby` = the column we filter by most (instrument_id) — speeds up reads.
    op.execute(
        "ALTER TABLE prices SET ("
        "timescaledb.compress, "
        "timescaledb.compress_segmentby = 'instrument_id, interval'"
        ");"
    )
    op.execute(
        "SELECT add_compression_policy('prices', INTERVAL '30 days');"
    )


def downgrade() -> None:
    """Downgrade schema."""
    # remove_compression_policy is forgiving if the policy doesn't exist
    op.execute("SELECT remove_compression_policy('prices', if_exists => true);")
    op.drop_table('prices')
