"""Add shared point-in-time provenance columns to the Silver tables.

Backward-compatible & additive:
  - Adds the canonical `data_quality_status` (ML enum: ok|stale|imputed|missing|
    invalid) to every Silver record, alongside the RETAINED legacy
    `validation_status` (auditability). Warning reasons already live in
    `data_quality_warnings`, so mapping clean_with_warnings -> ok loses nothing.
  - Adds the optional source-identity columns (source_dataset, source_series_id,
    source_url, license_policy_id) to the shared mixin. source_url is
    consolidated from the two tables that already had it.
  - Adds `source_available_at` to silver_price_bar (the one Silver table without
    the mixin) and backfills a point-in-time floor (end-of-day of the bar date).
  - Populates `source_available_at` on the mixin tables (= retrieved_at, a safe
    floor) where it was null.
  - Canonicalizes the market-price provider stored on bronze_ingestion_run
    (YahooFinanceProvider -> yahoo).

Data backfill fails closed: an unrecognized legacy status maps to `invalid`.

Revision ID: a1b2c3d4e5f6
Revises: f9a2c1d4e8b7
"""
from __future__ import annotations

import datetime as dt

import sqlalchemy as sa
from alembic import op

revision = "a1b2c3d4e5f6"
down_revision = "f9a2c1d4e8b7"
branch_labels = None
depends_on = None

# Every Silver table that inherits SilverRecordMixin.
_MIXIN_TABLES = (
    "silver_market_session",
    "silver_macro_series",
    "silver_macro_observation",
    "silver_economic_release",
    "silver_bea_value",
    "silver_security_identifier",
    "silver_company_filing",
    "silver_company_fact",
    "silver_material_event",
    "silver_security_master",
    "silver_short_sale_volume",
    "silver_short_interest",
)
# These two already carry a source_url column; don't re-add it.
_ALREADY_HAVE_SOURCE_URL = {"silver_company_filing", "silver_material_event"}

# Legacy validation_status / Gold status -> canonical ML data_quality_status.
# Mirrors catalystiq.provenance.contract.data_quality_status_from_validation;
# unknown values FAIL CLOSED to 'invalid'.
_QUALITY_BACKFILL_SQL = (
    "UPDATE {t} SET data_quality_status = CASE "
    "WHEN lower(validation_status) IN ('clean','clean_with_warnings','available','valid','ok','warning') THEN 'ok' "
    "WHEN lower(validation_status) IN ('insufficient_data','insufficient','missing') THEN 'missing' "
    "WHEN lower(validation_status) = 'imputed' THEN 'imputed' "
    "WHEN lower(validation_status) = 'stale' THEN 'stale' "
    "ELSE 'invalid' END"
)


def upgrade() -> None:
    for table in _MIXIN_TABLES:
        op.add_column(
            table,
            sa.Column("data_quality_status", sa.String(20), nullable=False, server_default="ok"),
        )
        op.add_column(table, sa.Column("source_dataset", sa.String(100), nullable=True))
        op.add_column(table, sa.Column("source_series_id", sa.String(100), nullable=True))
        op.add_column(table, sa.Column("license_policy_id", sa.String(50), nullable=True))
        if table not in _ALREADY_HAVE_SOURCE_URL:
            op.add_column(table, sa.Column("source_url", sa.String(500), nullable=True))

    op.add_column("silver_price_bar", sa.Column("source_available_at", sa.DateTime, nullable=True))

    # --- data backfill --------------------------------------------------
    for table in _MIXIN_TABLES:
        op.execute(_QUALITY_BACKFILL_SQL.format(t=table))
        op.execute(
            f"UPDATE {table} SET source_available_at = retrieved_at "
            "WHERE source_available_at IS NULL"
        )

    # Existing price bars: re-vocab quality and set a point-in-time availability
    # floor at end-of-day of the bar date (dialect-agnostic, done in Python).
    op.execute(
        "UPDATE silver_price_bar SET data_quality_status = 'ok' "
        "WHERE lower(data_quality_status) IN ('clean','clean_with_warnings')"
    )
    bind = op.get_bind()
    rows = bind.execute(
        sa.text("SELECT id, date FROM silver_price_bar WHERE source_available_at IS NULL")
    ).fetchall()
    for row_id, bar_date in rows:
        day = bar_date if isinstance(bar_date, dt.date) else dt.date.fromisoformat(str(bar_date)[:10])
        available = dt.datetime.combine(day, dt.time(23, 59, 59))
        bind.execute(
            sa.text("UPDATE silver_price_bar SET source_available_at = :a WHERE id = :i"),
            {"a": available, "i": row_id},
        )

    # Canonicalize the market-price provider recorded on the Bronze run.
    op.execute(
        "UPDATE bronze_ingestion_run SET provider = 'yahoo' WHERE provider = 'YahooFinanceProvider'"
    )


def downgrade() -> None:
    # Additive columns are dropped; the data-only backfills (quality re-vocab,
    # source_available_at population, provider canonicalization) are not
    # reversed - they are safe, canonical values.
    op.drop_column("silver_price_bar", "source_available_at")
    for table in _MIXIN_TABLES:
        if table not in _ALREADY_HAVE_SOURCE_URL:
            op.drop_column(table, "source_url")
        op.drop_column(table, "license_policy_id")
        op.drop_column(table, "source_series_id")
        op.drop_column(table, "source_dataset")
        op.drop_column(table, "data_quality_status")
