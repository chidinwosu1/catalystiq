"""add provider_comparison (cross-provider validation results)

Revision ID: d5f19a3c8b40
Revises: c9d3a1b7f402
Create Date: 2026-07-18 09:00:00.000000

Records a primary-vs-secondary market-data comparison (§5, §16): both values,
their difference, whether it's within tolerance, and which was selected and
why. Values are never averaged.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d5f19a3c8b40"
down_revision: Union[str, Sequence[str], None] = "c9d3a1b7f402"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "provider_comparison",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("domain", sa.String(length=30), nullable=False),
        sa.Column("symbol", sa.String(length=15), nullable=False),
        sa.Column("field", sa.String(length=30), nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=True),
        sa.Column("primary_provider", sa.String(length=30), nullable=False),
        sa.Column("primary_value", sa.Float(), nullable=True),
        sa.Column("primary_timestamp", sa.DateTime(), nullable=True),
        sa.Column("secondary_provider", sa.String(length=30), nullable=False),
        sa.Column("secondary_value", sa.Float(), nullable=True),
        sa.Column("secondary_timestamp", sa.DateTime(), nullable=True),
        sa.Column("absolute_diff", sa.Float(), nullable=True),
        sa.Column("relative_diff_pct", sa.Float(), nullable=True),
        sa.Column("tolerance_pct", sa.Float(), nullable=False),
        sa.Column("within_tolerance", sa.Boolean(), nullable=False),
        sa.Column("selected_provider", sa.String(length=30), nullable=False),
        sa.Column("selected_reason", sa.String(length=300), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_provider_comparison_domain"), "provider_comparison", ["domain"], unique=False)
    op.create_index(op.f("ix_provider_comparison_symbol"), "provider_comparison", ["symbol"], unique=False)
    op.create_index(op.f("ix_provider_comparison_within_tolerance"), "provider_comparison", ["within_tolerance"], unique=False)
    op.create_index(op.f("ix_provider_comparison_created_at"), "provider_comparison", ["created_at"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_provider_comparison_created_at"), table_name="provider_comparison")
    op.drop_index(op.f("ix_provider_comparison_within_tolerance"), table_name="provider_comparison")
    op.drop_index(op.f("ix_provider_comparison_symbol"), table_name="provider_comparison")
    op.drop_index(op.f("ix_provider_comparison_domain"), table_name="provider_comparison")
    op.drop_table("provider_comparison")
