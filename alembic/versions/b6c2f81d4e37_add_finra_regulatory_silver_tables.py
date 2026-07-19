"""add FINRA regulatory Silver tables (short_sale_volume, short_interest)

Revision ID: b6c2f81d4e37
Revises: a1e7c3f5920d
Create Date: 2026-07-18 07:00:00.000000

Daily short-sale volume and semi-monthly short interest as SEPARATE Silver
products (§11). file_version is part of each identity so a corrected FINRA
file is preserved alongside the original.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b6c2f81d4e37"
down_revision: Union[str, Sequence[str], None] = "a1e7c3f5920d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _mixin_columns() -> list:
    return [
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
    ]


def upgrade() -> None:
    op.create_table(
        "silver_short_sale_volume",
        *_mixin_columns(),
        sa.Column("symbol", sa.String(length=15), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("short_volume", sa.Integer(), nullable=True),
        sa.Column("short_exempt_volume", sa.Integer(), nullable=True),
        sa.Column("total_volume", sa.Integer(), nullable=True),
        sa.Column("reporting_facility", sa.String(length=20), nullable=True),
        sa.Column("file_version", sa.String(length=20), nullable=False),
        sa.ForeignKeyConstraint(["bronze_ingestion_run_id"], ["bronze_ingestion_run.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "provider", "symbol", "trade_date", "reporting_facility", "file_version",
            name="uq_silver_short_sale_volume",
        ),
    )
    op.create_index(op.f("ix_silver_short_sale_volume_stable_identifier"), "silver_short_sale_volume", ["stable_identifier"], unique=False)
    op.create_index(op.f("ix_silver_short_sale_volume_symbol"), "silver_short_sale_volume", ["symbol"], unique=False)
    op.create_index(op.f("ix_silver_short_sale_volume_trade_date"), "silver_short_sale_volume", ["trade_date"], unique=False)

    op.create_table(
        "silver_short_interest",
        *_mixin_columns(),
        sa.Column("symbol", sa.String(length=15), nullable=False),
        sa.Column("settlement_date", sa.Date(), nullable=False),
        sa.Column("publication_date", sa.Date(), nullable=True),
        sa.Column("short_interest_quantity", sa.Integer(), nullable=True),
        sa.Column("previous_short_interest_quantity", sa.Integer(), nullable=True),
        sa.Column("average_daily_volume", sa.Float(), nullable=True),
        sa.Column("days_to_cover", sa.Float(), nullable=True),
        sa.Column("file_version", sa.String(length=20), nullable=False),
        sa.ForeignKeyConstraint(["bronze_ingestion_run_id"], ["bronze_ingestion_run.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "provider", "symbol", "settlement_date", "file_version",
            name="uq_silver_short_interest",
        ),
    )
    op.create_index(op.f("ix_silver_short_interest_stable_identifier"), "silver_short_interest", ["stable_identifier"], unique=False)
    op.create_index(op.f("ix_silver_short_interest_symbol"), "silver_short_interest", ["symbol"], unique=False)
    op.create_index(op.f("ix_silver_short_interest_settlement_date"), "silver_short_interest", ["settlement_date"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_silver_short_interest_settlement_date"), table_name="silver_short_interest")
    op.drop_index(op.f("ix_silver_short_interest_symbol"), table_name="silver_short_interest")
    op.drop_index(op.f("ix_silver_short_interest_stable_identifier"), table_name="silver_short_interest")
    op.drop_table("silver_short_interest")

    op.drop_index(op.f("ix_silver_short_sale_volume_trade_date"), table_name="silver_short_sale_volume")
    op.drop_index(op.f("ix_silver_short_sale_volume_symbol"), table_name="silver_short_sale_volume")
    op.drop_index(op.f("ix_silver_short_sale_volume_stable_identifier"), table_name="silver_short_sale_volume")
    op.drop_table("silver_short_sale_volume")
