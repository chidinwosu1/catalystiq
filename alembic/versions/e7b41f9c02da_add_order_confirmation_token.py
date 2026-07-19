"""add order_confirmation_token (single-use order confirmations)

Revision ID: e7b41f9c02da
Revises: d5f19a3c8b40
Create Date: 2026-07-18 10:00:00.000000

Single-use, short-lived confirmation tokens bound to exact order details
(§13). Submission consumes a token (used_at); a replay or a parameter change
is rejected.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e7b41f9c02da"
down_revision: Union[str, Sequence[str], None] = "d5f19a3c8b40"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "order_confirmation_token",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("jti", sa.String(length=40), nullable=False),
        sa.Column("fingerprint", sa.String(length=1000), nullable=False),
        sa.Column("account_id", sa.String(length=100), nullable=False),
        sa.Column("mode", sa.String(length=10), nullable=False),
        sa.Column("estimated_max_loss", sa.Float(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("used_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("jti", name="uq_order_confirmation_token_jti"),
    )
    op.create_index(
        op.f("ix_order_confirmation_token_jti"), "order_confirmation_token", ["jti"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_order_confirmation_token_jti"), table_name="order_confirmation_token")
    op.drop_table("order_confirmation_token")
