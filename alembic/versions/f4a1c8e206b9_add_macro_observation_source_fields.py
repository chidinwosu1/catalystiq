"""add source_fields to silver_macro_observation (BLS-specific metadata)

Revision ID: f4a1c8e206b9
Revises: e3b9d1c72a08
Create Date: 2026-07-18 05:00:00.000000

BLS observations normalize into the same macro-observation Silver model as
FRED (§8); this nullable JSON column preserves BLS-specific source fields
(period code, footnotes, preliminary/revised flag) without a separate table.
Additive - FRED rows leave it null.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f4a1c8e206b9"
down_revision: Union[str, Sequence[str], None] = "e3b9d1c72a08"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("silver_macro_observation", schema=None) as batch_op:
        batch_op.add_column(sa.Column("source_fields", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("silver_macro_observation", schema=None) as batch_op:
        batch_op.drop_column("source_fields")
