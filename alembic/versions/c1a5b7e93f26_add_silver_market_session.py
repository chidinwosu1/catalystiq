"""add silver_market_session (market-calendar Silver product)

Revision ID: c1a5b7e93f26
Revises: b8d2e4f60a13
Create Date: 2026-07-18 02:00:00.000000

The normalized market-session product (§10): one row per exchange trading
session, upserted on (exchange, session_date). Columns are the SilverRecord
common set (catalystiq/db/models.py's SilverRecordMixin) plus the
session-specific fields.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c1a5b7e93f26"
down_revision: Union[str, Sequence[str], None] = "b8d2e4f60a13"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "silver_market_session",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("stable_identifier", sa.String(length=100), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("source_record_id", sa.String(length=255), nullable=True),
        sa.Column("source_available_at", sa.DateTime(), nullable=True),
        sa.Column("effective_at", sa.DateTime(), nullable=True),
        sa.Column("retrieved_at", sa.DateTime(), nullable=False),
        sa.Column("bronze_ingestion_run_id", sa.Integer(), nullable=True),
        sa.Column("validation_status", sa.String(length=20), nullable=False),
        sa.Column("data_quality_warnings", sa.JSON(), nullable=True),
        sa.Column("normalization_version", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("exchange", sa.String(length=15), nullable=False),
        sa.Column("session_date", sa.Date(), nullable=False),
        sa.Column("open_at", sa.DateTime(), nullable=True),
        sa.Column("close_at", sa.DateTime(), nullable=True),
        sa.Column("timezone", sa.String(length=40), nullable=False),
        sa.Column("early_close", sa.Boolean(), nullable=False),
        sa.Column("holiday_name", sa.String(length=100), nullable=True),
        sa.Column("calendar_version", sa.String(length=30), nullable=False),
        sa.ForeignKeyConstraint(["bronze_ingestion_run_id"], ["bronze_ingestion_run.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("exchange", "session_date", name="uq_silver_market_session"),
    )
    op.create_index(
        op.f("ix_silver_market_session_stable_identifier"),
        "silver_market_session",
        ["stable_identifier"],
        unique=False,
    )
    op.create_index(
        op.f("ix_silver_market_session_exchange"),
        "silver_market_session",
        ["exchange"],
        unique=False,
    )
    op.create_index(
        op.f("ix_silver_market_session_session_date"),
        "silver_market_session",
        ["session_date"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_silver_market_session_session_date"), table_name="silver_market_session")
    op.drop_index(op.f("ix_silver_market_session_exchange"), table_name="silver_market_session")
    op.drop_index(
        op.f("ix_silver_market_session_stable_identifier"), table_name="silver_market_session"
    )
    op.drop_table("silver_market_session")
