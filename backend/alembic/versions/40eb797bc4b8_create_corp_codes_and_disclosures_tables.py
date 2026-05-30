"""create corp_codes and disclosures tables

Revision ID: 40eb797bc4b8
Revises: a19561321a31
Create Date: 2026-05-30 15:54:03.313871

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '40eb797bc4b8'
down_revision: Union[str, Sequence[str], None] = 'a19561321a31'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "corp_codes",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("source_corp_id", sa.String(length=32), nullable=False),
        sa.Column("exchange", sa.String(length=8), nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=True),
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
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source", "source_corp_id", name="uq_corp_codes_source_id"
        ),
    )
    op.create_index(
        "ix_corp_codes_exchange_symbol",
        "corp_codes",
        ["exchange", "symbol"],
        unique=False,
    )

    op.create_table(
        "disclosures",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("instrument_id", sa.BigInteger(), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("source_id", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("filed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("report_type", sa.String(length=64), nullable=True),
        sa.Column("submitter", sa.String(length=255), nullable=True),
        sa.Column("raw_url", sa.String(length=512), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["instrument_id"], ["instruments.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source", "source_id", name="uq_disclosures_source_id"
        ),
    )
    op.create_index(
        "ix_disclosures_instrument_filed_at",
        "disclosures",
        ["instrument_id", "filed_at"],
        unique=False,
    )
    # NOTE: Alembic autogenerate also tried to drop `prices_time_idx`. That
    # index is auto-created by TimescaleDB's create_hypertable() and is not
    # represented in our ORM models. The drop was removed manually here;
    # same workaround as the watchlist migration (a19561321a31).


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_disclosures_instrument_filed_at", table_name="disclosures")
    op.drop_table("disclosures")
    op.drop_index("ix_corp_codes_exchange_symbol", table_name="corp_codes")
    op.drop_table("corp_codes")
