"""add silver_security_master (Nasdaq Trader symbol directory)

Revision ID: c9d3a1b7f402
Revises: b6c2f81d4e37
Create Date: 2026-07-18 08:00:00.000000

The security master / symbol directory Silver product (§12, §14 #1), keyed on
a stable internal security id rather than the ticker alone.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c9d3a1b7f402"
down_revision: Union[str, Sequence[str], None] = "b6c2f81d4e37"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "silver_security_master",
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
        sa.Column("internal_security_id", sa.String(length=60), nullable=False),
        sa.Column("symbol", sa.String(length=15), nullable=False),
        sa.Column("name", sa.String(length=300), nullable=True),
        sa.Column("exchange", sa.String(length=20), nullable=True),
        sa.Column("listing_market", sa.String(length=20), nullable=True),
        sa.Column("etf", sa.Boolean(), nullable=True),
        sa.Column("test_issue", sa.Boolean(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["bronze_ingestion_run_id"], ["bronze_ingestion_run.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider", "internal_security_id", name="uq_silver_security_master"),
    )
    op.create_index(op.f("ix_silver_security_master_stable_identifier"), "silver_security_master", ["stable_identifier"], unique=False)
    op.create_index(op.f("ix_silver_security_master_internal_security_id"), "silver_security_master", ["internal_security_id"], unique=False)
    op.create_index(op.f("ix_silver_security_master_symbol"), "silver_security_master", ["symbol"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_silver_security_master_symbol"), table_name="silver_security_master")
    op.drop_index(op.f("ix_silver_security_master_internal_security_id"), table_name="silver_security_master")
    op.drop_index(op.f("ix_silver_security_master_stable_identifier"), table_name="silver_security_master")
    op.drop_table("silver_security_master")
