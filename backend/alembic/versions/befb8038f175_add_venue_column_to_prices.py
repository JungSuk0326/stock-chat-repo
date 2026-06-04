"""add venue column to prices + extend PK

Revision ID: befb8038f175
Revises: 433bc73a2b38
Create Date: 2026-06-05 01:21:17.790991

NXT(넥스트레이드) ATS launched 2025-03 trades the same KOSPI/KOSDAQ symbols
on a separate venue with its own price/volume. To store both KRX and NXT
bars at the same (instrument, interval, time), the primary key extends to
include `venue`. Existing rows are stamped 'KRX' via the column default.

TimescaleDB note: `prices` is a hypertable partitioned on `time`. The PK
must include the partition column, which is satisfied here.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'befb8038f175'
down_revision: Union[str, Sequence[str], None] = '433bc73a2b38'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Add the column with a default so existing KRX rows get stamped.
    op.add_column(
        "prices",
        sa.Column(
            "venue", sa.String(length=8), server_default="KRX", nullable=False
        ),
    )
    # 2. Drop the old PK and recreate with `venue` included.
    op.drop_constraint("pk_prices", "prices", type_="primary")
    op.create_primary_key(
        "pk_prices",
        "prices",
        ["instrument_id", "interval", "venue", "time"],
    )


def downgrade() -> None:
    # Reverse: drop the wider PK, drop the column, restore the old PK.
    # Lossy if NXT rows exist — they'd lose uniqueness against KRX rows
    # at the same minute. Acceptable for a dev rollback.
    op.drop_constraint("pk_prices", "prices", type_="primary")
    op.drop_column("prices", "venue")
    op.create_primary_key(
        "pk_prices",
        "prices",
        ["instrument_id", "interval", "time"],
    )
