"""add gold_reference_check audit table and flagged_for_reference_check

Revision ID: 0f5ca6b55d14
Revises: 8eb225079c06
Create Date: 2026-07-17 21:00:00.000000

Adds the reference-calculation adapter's audit trail
(catalystiq/validation/reference/): one gold_reference_check row per
indicator per check run, plus a flagged_for_reference_check /
reference_checked_at pair on gold_calculation_run so the synchronous
anomaly sanity check can hand a run off to the async reference-validation
loop without touching the request path itself.

No schema change needed for the "quarantined" data_quality_status value -
that column is already a free-text String(20) on every gold_* table.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0f5ca6b55d14"
down_revision: Union[str, Sequence[str], None] = "8eb225079c06"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("gold_calculation_run", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "flagged_for_reference_check", sa.Boolean(), nullable=False, server_default=sa.false()
            )
        )
        batch_op.add_column(sa.Column("reference_checked_at", sa.DateTime(), nullable=True))
    op.create_index(
        op.f("ix_gold_calculation_run_flagged_for_reference_check"),
        "gold_calculation_run",
        ["flagged_for_reference_check"],
        unique=False,
    )

    op.create_table(
        "gold_reference_check",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("gold_calculation_run_id", sa.Integer(), nullable=False),
        sa.Column("ticker_id", sa.Integer(), nullable=False),
        sa.Column("indicator_name", sa.String(length=100), nullable=False),
        sa.Column("reference_source", sa.String(length=30), nullable=False),
        sa.Column("reference_library", sa.String(length=50), nullable=False),
        sa.Column("reference_library_version", sa.String(length=20), nullable=False),
        sa.Column("parameters", sa.JSON(), nullable=False),
        sa.Column("expected_value", sa.Float(), nullable=True),
        sa.Column("actual_value", sa.Float(), nullable=True),
        sa.Column("absolute_diff", sa.Float(), nullable=True),
        sa.Column("relative_diff", sa.Float(), nullable=True),
        sa.Column("tolerance_abs", sa.Float(), nullable=True),
        sa.Column("tolerance_rel", sa.Float(), nullable=True),
        sa.Column("warmup_bars_excluded", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("discrepancy_reason", sa.String(length=1000), nullable=True),
        sa.Column("checked_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["gold_calculation_run_id"], ["gold_calculation_run.id"]),
        sa.ForeignKeyConstraint(["ticker_id"], ["tickers.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_gold_reference_check_gold_calculation_run_id"),
        "gold_reference_check",
        ["gold_calculation_run_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_gold_reference_check_ticker_id"), "gold_reference_check", ["ticker_id"], unique=False
    )
    op.create_index(
        op.f("ix_gold_reference_check_indicator_name"),
        "gold_reference_check",
        ["indicator_name"],
        unique=False,
    )
    op.create_index(
        op.f("ix_gold_reference_check_status"), "gold_reference_check", ["status"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_gold_reference_check_status"), table_name="gold_reference_check")
    op.drop_index(
        op.f("ix_gold_reference_check_indicator_name"), table_name="gold_reference_check"
    )
    op.drop_index(op.f("ix_gold_reference_check_ticker_id"), table_name="gold_reference_check")
    op.drop_index(
        op.f("ix_gold_reference_check_gold_calculation_run_id"), table_name="gold_reference_check"
    )
    op.drop_table("gold_reference_check")

    op.drop_index(
        op.f("ix_gold_calculation_run_flagged_for_reference_check"),
        table_name="gold_calculation_run",
    )
    with op.batch_alter_table("gold_calculation_run", schema=None) as batch_op:
        batch_op.drop_column("reference_checked_at")
        batch_op.drop_column("flagged_for_reference_check")
