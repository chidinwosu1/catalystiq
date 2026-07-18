"""add silver_bea_value (BEA Silver product)

Revision ID: a1e7c3f5920d
Revises: f4a1c8e206b9
Create Date: 2026-07-18 06:00:00.000000

The BEA Silver product (§9): table/line-oriented values, idempotent on
(provider, dataset, table_name, line_number, time_period, frequency).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a1e7c3f5920d"
down_revision: Union[str, Sequence[str], None] = "f4a1c8e206b9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "silver_bea_value",
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
        sa.Column("dataset", sa.String(length=30), nullable=False),
        sa.Column("table_name", sa.String(length=40), nullable=False),
        sa.Column("line_number", sa.String(length=20), nullable=True),
        sa.Column("line_description", sa.String(length=300), nullable=True),
        sa.Column("series_code", sa.String(length=40), nullable=True),
        sa.Column("time_period", sa.String(length=20), nullable=False),
        sa.Column("frequency", sa.String(length=10), nullable=True),
        sa.Column("value", sa.Float(), nullable=True),
        sa.Column("unit", sa.String(length=60), nullable=True),
        sa.Column("scale", sa.String(length=20), nullable=True),
        sa.ForeignKeyConstraint(["bronze_ingestion_run_id"], ["bronze_ingestion_run.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "provider", "dataset", "table_name", "line_number", "time_period", "frequency",
            name="uq_silver_bea_value",
        ),
    )
    op.create_index(op.f("ix_silver_bea_value_stable_identifier"), "silver_bea_value", ["stable_identifier"], unique=False)
    op.create_index(op.f("ix_silver_bea_value_dataset"), "silver_bea_value", ["dataset"], unique=False)
    op.create_index(op.f("ix_silver_bea_value_table_name"), "silver_bea_value", ["table_name"], unique=False)
    op.create_index(op.f("ix_silver_bea_value_time_period"), "silver_bea_value", ["time_period"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_silver_bea_value_time_period"), table_name="silver_bea_value")
    op.drop_index(op.f("ix_silver_bea_value_table_name"), table_name="silver_bea_value")
    op.drop_index(op.f("ix_silver_bea_value_dataset"), table_name="silver_bea_value")
    op.drop_index(op.f("ix_silver_bea_value_stable_identifier"), table_name="silver_bea_value")
    op.drop_table("silver_bea_value")
