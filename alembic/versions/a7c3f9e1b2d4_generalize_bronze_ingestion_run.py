"""generalize bronze_ingestion_run for all data domains

Revision ID: a7c3f9e1b2d4
Revises: 0f5ca6b55d14
Create Date: 2026-07-18 00:00:00.000000

Adds the domain-agnostic ingestion-run fields from the data-source spec (§3)
to bronze_ingestion_run so the one table can capture ingestion for every
provider domain (fundamentals, macro, regulatory, ...), not just the
market_price path.

Purely additive: every new column is nullable (or has a server default), so
the existing price-bar pipeline - which keeps writing requested_symbol and
bars_fetched - is untouched, and the migration needs no data backfill. New
statuses rate_limited/unavailable need no schema change (status is already a
free-text String(20)).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "a7c3f9e1b2d4"
down_revision: Union[str, Sequence[str], None] = "0f5ca6b55d14"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("bronze_ingestion_run", schema=None) as batch_op:
        batch_op.add_column(sa.Column("requested_identifier", sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column("dataset", sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column("endpoint", sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column("data_classification", sa.String(length=20), nullable=True))
        batch_op.add_column(sa.Column("license_classification", sa.String(length=30), nullable=True))
        batch_op.add_column(sa.Column("response_timestamp", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("release_timestamp", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("http_status", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("record_count", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("rate_limit_info", sa.JSON(), nullable=True))
        batch_op.add_column(
            sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0")
        )
        batch_op.add_column(sa.Column("error_category", sa.String(length=30), nullable=True))
        batch_op.add_column(sa.Column("payload_checksum", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("payload_reference", sa.String(length=500), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("bronze_ingestion_run", schema=None) as batch_op:
        batch_op.drop_column("payload_reference")
        batch_op.drop_column("payload_checksum")
        batch_op.drop_column("error_category")
        batch_op.drop_column("retry_count")
        batch_op.drop_column("rate_limit_info")
        batch_op.drop_column("record_count")
        batch_op.drop_column("http_status")
        batch_op.drop_column("release_timestamp")
        batch_op.drop_column("response_timestamp")
        batch_op.drop_column("license_classification")
        batch_op.drop_column("data_classification")
        batch_op.drop_column("endpoint")
        batch_op.drop_column("dataset")
        batch_op.drop_column("requested_identifier")
