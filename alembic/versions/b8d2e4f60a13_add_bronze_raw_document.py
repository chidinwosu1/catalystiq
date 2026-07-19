"""add bronze_raw_document generic raw-payload store

Revision ID: b8d2e4f60a13
Revises: a7c3f9e1b2d4
Create Date: 2026-07-18 01:00:00.000000

Generic append-only store for raw provider payloads from the document/record
domains added in Phase 2+ (NYSE calendar, FRED/ALFRED, SEC EDGAR), so those
domains don't each need a bespoke Bronze table. See
catalystiq/pipelines/ingestion.py.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b8d2e4f60a13"
down_revision: Union[str, Sequence[str], None] = "a7c3f9e1b2d4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "bronze_raw_document",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("ingestion_run_id", sa.Integer(), nullable=False),
        sa.Column("domain", sa.String(length=50), nullable=False),
        sa.Column("source_identifier", sa.String(length=100), nullable=False),
        sa.Column("document_type", sa.String(length=50), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("payload_checksum", sa.String(length=64), nullable=False),
        sa.Column("source_url", sa.String(length=1000), nullable=True),
        sa.Column("source_timestamp", sa.DateTime(), nullable=True),
        sa.Column("fetched_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["ingestion_run_id"], ["bronze_ingestion_run.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_bronze_raw_document_ingestion_run_id"),
        "bronze_raw_document",
        ["ingestion_run_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_bronze_raw_document_domain"), "bronze_raw_document", ["domain"], unique=False
    )
    op.create_index(
        op.f("ix_bronze_raw_document_source_identifier"),
        "bronze_raw_document",
        ["source_identifier"],
        unique=False,
    )
    op.create_index(
        op.f("ix_bronze_raw_document_payload_checksum"),
        "bronze_raw_document",
        ["payload_checksum"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_bronze_raw_document_payload_checksum"), table_name="bronze_raw_document")
    op.drop_index(op.f("ix_bronze_raw_document_source_identifier"), table_name="bronze_raw_document")
    op.drop_index(op.f("ix_bronze_raw_document_domain"), table_name="bronze_raw_document")
    op.drop_index(op.f("ix_bronze_raw_document_ingestion_run_id"), table_name="bronze_raw_document")
    op.drop_table("bronze_raw_document")
