"""add fundamentals (SEC EDGAR) Silver tables

Revision ID: e3b9d1c72a08
Revises: d2f6a8c04b71
Create Date: 2026-07-18 04:00:00.000000

The fundamentals Silver products (§6): security_identifier (ticker<->CIK),
company_filing, company_fact (XBRL; also serves as the financial-statement
fact), and material_event (8-K). Facts/filings are keyed by accession number
so an amendment lands as a new row and never overwrites the originally-filed
value. All carry the SilverRecordMixin common columns.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e3b9d1c72a08"
down_revision: Union[str, Sequence[str], None] = "d2f6a8c04b71"
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


def _fk_run() -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(["bronze_ingestion_run_id"], ["bronze_ingestion_run.id"])


def upgrade() -> None:
    op.create_table(
        "silver_security_identifier",
        *_mixin_columns(),
        sa.Column("cik", sa.String(length=10), nullable=False),
        sa.Column("symbol", sa.String(length=15), nullable=False),
        sa.Column("name", sa.String(length=300), nullable=True),
        _fk_run(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider", "cik", name="uq_silver_security_identifier"),
    )
    op.create_index(op.f("ix_silver_security_identifier_stable_identifier"), "silver_security_identifier", ["stable_identifier"], unique=False)
    op.create_index(op.f("ix_silver_security_identifier_cik"), "silver_security_identifier", ["cik"], unique=False)
    op.create_index(op.f("ix_silver_security_identifier_symbol"), "silver_security_identifier", ["symbol"], unique=False)

    op.create_table(
        "silver_company_filing",
        *_mixin_columns(),
        sa.Column("cik", sa.String(length=10), nullable=False),
        sa.Column("symbol", sa.String(length=15), nullable=True),
        sa.Column("form", sa.String(length=20), nullable=False),
        sa.Column("accession_number", sa.String(length=30), nullable=False),
        sa.Column("filing_date", sa.Date(), nullable=True),
        sa.Column("acceptance_at", sa.DateTime(), nullable=True),
        sa.Column("report_date", sa.Date(), nullable=True),
        sa.Column("primary_document", sa.String(length=255), nullable=True),
        sa.Column("primary_doc_description", sa.String(length=255), nullable=True),
        sa.Column("is_amendment", sa.Boolean(), nullable=False),
        sa.Column("source_url", sa.String(length=500), nullable=True),
        _fk_run(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider", "accession_number", name="uq_silver_company_filing"),
    )
    op.create_index(op.f("ix_silver_company_filing_stable_identifier"), "silver_company_filing", ["stable_identifier"], unique=False)
    op.create_index(op.f("ix_silver_company_filing_cik"), "silver_company_filing", ["cik"], unique=False)
    op.create_index(op.f("ix_silver_company_filing_form"), "silver_company_filing", ["form"], unique=False)
    op.create_index(op.f("ix_silver_company_filing_accession_number"), "silver_company_filing", ["accession_number"], unique=False)
    op.create_index(op.f("ix_silver_company_filing_filing_date"), "silver_company_filing", ["filing_date"], unique=False)

    op.create_table(
        "silver_company_fact",
        *_mixin_columns(),
        sa.Column("cik", sa.String(length=10), nullable=False),
        sa.Column("taxonomy", sa.String(length=30), nullable=False),
        sa.Column("concept", sa.String(length=120), nullable=False),
        sa.Column("unit", sa.String(length=30), nullable=False),
        sa.Column("value", sa.Float(), nullable=True),
        sa.Column("fiscal_year", sa.Integer(), nullable=True),
        sa.Column("fiscal_period", sa.String(length=10), nullable=True),
        sa.Column("period_start", sa.Date(), nullable=True),
        sa.Column("period_end", sa.Date(), nullable=True),
        sa.Column("form", sa.String(length=20), nullable=True),
        sa.Column("filing_date", sa.Date(), nullable=True),
        sa.Column("accession_number", sa.String(length=30), nullable=True),
        sa.Column("is_amendment", sa.Boolean(), nullable=False),
        sa.Column("frame", sa.String(length=50), nullable=True),
        _fk_run(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "provider", "cik", "accession_number", "taxonomy", "concept", "unit",
            "period_start", "period_end", name="uq_silver_company_fact",
        ),
    )
    op.create_index(op.f("ix_silver_company_fact_stable_identifier"), "silver_company_fact", ["stable_identifier"], unique=False)
    op.create_index(op.f("ix_silver_company_fact_cik"), "silver_company_fact", ["cik"], unique=False)
    op.create_index(op.f("ix_silver_company_fact_concept"), "silver_company_fact", ["concept"], unique=False)

    op.create_table(
        "silver_material_event",
        *_mixin_columns(),
        sa.Column("cik", sa.String(length=10), nullable=False),
        sa.Column("symbol", sa.String(length=15), nullable=True),
        sa.Column("accession_number", sa.String(length=30), nullable=False),
        sa.Column("form", sa.String(length=20), nullable=False),
        sa.Column("filing_date", sa.Date(), nullable=True),
        sa.Column("acceptance_at", sa.DateTime(), nullable=True),
        sa.Column("items", sa.JSON(), nullable=True),
        sa.Column("is_amendment", sa.Boolean(), nullable=False),
        sa.Column("source_url", sa.String(length=500), nullable=True),
        _fk_run(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider", "accession_number", name="uq_silver_material_event"),
    )
    op.create_index(op.f("ix_silver_material_event_stable_identifier"), "silver_material_event", ["stable_identifier"], unique=False)
    op.create_index(op.f("ix_silver_material_event_cik"), "silver_material_event", ["cik"], unique=False)
    op.create_index(op.f("ix_silver_material_event_accession_number"), "silver_material_event", ["accession_number"], unique=False)
    op.create_index(op.f("ix_silver_material_event_filing_date"), "silver_material_event", ["filing_date"], unique=False)


def downgrade() -> None:
    for idx in (
        "ix_silver_material_event_filing_date", "ix_silver_material_event_accession_number",
        "ix_silver_material_event_cik", "ix_silver_material_event_stable_identifier",
    ):
        op.drop_index(op.f(idx), table_name="silver_material_event")
    op.drop_table("silver_material_event")

    for idx in (
        "ix_silver_company_fact_concept", "ix_silver_company_fact_cik",
        "ix_silver_company_fact_stable_identifier",
    ):
        op.drop_index(op.f(idx), table_name="silver_company_fact")
    op.drop_table("silver_company_fact")

    for idx in (
        "ix_silver_company_filing_filing_date", "ix_silver_company_filing_accession_number",
        "ix_silver_company_filing_form", "ix_silver_company_filing_cik",
        "ix_silver_company_filing_stable_identifier",
    ):
        op.drop_index(op.f(idx), table_name="silver_company_filing")
    op.drop_table("silver_company_filing")

    for idx in (
        "ix_silver_security_identifier_symbol", "ix_silver_security_identifier_cik",
        "ix_silver_security_identifier_stable_identifier",
    ):
        op.drop_index(op.f(idx), table_name="silver_security_identifier")
    op.drop_table("silver_security_identifier")
