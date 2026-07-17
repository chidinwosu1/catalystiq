"""harden medallion lineage, reproducibility, and config versioning

Revision ID: 8eb225079c06
Revises: bd2ffb846c76
Create Date: 2026-07-17 20:00:00.000000

Hardening pass on top of the first medallion migration (bd2ffb846c76, left
untouched):

  - bronze_ingestion_run: symbol -> requested_symbol, plus full request
    metadata (interval, date range, request_params, provider adapter
    version). Nothing has ever read this column by name outside the ORM
    (confirmed - no raw SQL references it), so the rename is safe.
  - new bronze_market_quote: a fetched-then-discarded live quote is now
    persisted instead.
  - new silver_build_run / silver_build_run_bronze_ingestion_run /
    silver_build_run_bar: an immutable audit trail of every Silver build,
    independent of the live (upsert-in-place) silver_price_bar table -
    this is what makes an old Gold snapshot reproducible after newer
    Bronze/Silver data arrives.
  - silver_price_bar_rejected: add silver_build_run_id for the same
    build-traceability.
  - data migration: silver_price_bar.data_quality_status 'flagged' ->
    'clean_with_warnings' (spec vocabulary; no schema change).
  - new gold_calculation_run / gold_calculation_run_dependency: run-level
    lineage for Gold, including multi-symbol dependencies (Risk's
    benchmark, Market Context's market/sector ETFs) that were previously
    invisible outside the primary symbol's own lineage.
  - all 5 gold_* tables: add gold_calculation_run_id, configuration_version,
    timeframe, silver_build_run_id; unique constraint widens from
    (ticker_id, date, calculation_version) to (ticker_id, date, timeframe,
    calculation_version, configuration_version, silver_build_run_id).

Nothing in any of these tables has ever been written to outside this
session's own test runs (confirmed in the first migration's docstring and
unchanged since), so this is a safe structural migration, not a data
migration, aside from the one explicit status-value UPDATE noted above.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "8eb225079c06"
down_revision: Union[str, Sequence[str], None] = "bd2ffb846c76"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# (table_name, current_constraint_name_in_db, canonical_constraint_name).
# The first medallion migration (bd2ffb846c76) renamed 4 of these 5 tables
# from their pre-medallion names but never renamed their unique
# constraints along with them - gold_technical_snapshot was created fresh
# with the right name; the other four still carry their old, un-prefixed
# constraint name at the DB level even though the ORM model
# (catalystiq/db/models.py) has always declared the "uq_gold_*" name.
# Corrected here rather than by editing that already-applied migration.
_GOLD_TABLES = [
    ("gold_technical_snapshot", "uq_gold_technical_snapshot", "uq_gold_technical_snapshot"),
    ("gold_market_structure_snapshot", "uq_market_structure_snapshot", "uq_gold_market_structure_snapshot"),
    ("gold_risk_snapshot", "uq_risk_snapshot", "uq_gold_risk_snapshot"),
    ("gold_volume_liquidity_snapshot", "uq_volume_liquidity_snapshot", "uq_gold_volume_liquidity_snapshot"),
    ("gold_market_context_snapshot", "uq_market_context_snapshot", "uq_gold_market_context_snapshot"),
]


def upgrade() -> None:
    # --- bronze_ingestion_run: rename + request metadata --------------------
    with op.batch_alter_table("bronze_ingestion_run", schema=None) as batch_op:
        batch_op.alter_column("symbol", new_column_name="requested_symbol")
        batch_op.add_column(
            sa.Column("requested_interval", sa.String(length=10), nullable=False, server_default="1d")
        )
        batch_op.add_column(sa.Column("requested_start", sa.Date(), nullable=True))
        batch_op.add_column(sa.Column("requested_end", sa.Date(), nullable=True))
        batch_op.add_column(sa.Column("request_params", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("provider_adapter_version", sa.String(length=20), nullable=True))
        batch_op.add_column(sa.Column("provider_timezone", sa.String(length=50), nullable=True))

    op.drop_index(op.f("ix_bronze_ingestion_run_symbol"), table_name="bronze_ingestion_run")
    op.create_index(
        op.f("ix_bronze_ingestion_run_requested_symbol"),
        "bronze_ingestion_run",
        ["requested_symbol"],
        unique=False,
    )

    # --- bronze_market_quote (new) ------------------------------------------
    op.create_table(
        "bronze_market_quote",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("ingestion_run_id", sa.Integer(), nullable=True),
        sa.Column("ticker_id", sa.Integer(), nullable=False),
        sa.Column("source_symbol", sa.String(length=15), nullable=False),
        sa.Column("price", sa.Float(), nullable=False),
        sa.Column("previous_close", sa.Float(), nullable=True),
        sa.Column("quote_as_of", sa.DateTime(), nullable=False),
        sa.Column("raw_payload", sa.JSON(), nullable=False),
        sa.Column("ingested_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["ingestion_run_id"], ["bronze_ingestion_run.id"]),
        sa.ForeignKeyConstraint(["ticker_id"], ["tickers.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_bronze_market_quote_ingestion_run_id"),
        "bronze_market_quote",
        ["ingestion_run_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_bronze_market_quote_ticker_id"), "bronze_market_quote", ["ticker_id"], unique=False
    )

    # --- silver_build_run + association + immutable bar snapshot -----------
    op.create_table(
        "silver_build_run",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("ticker_id", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("bars_upserted", sa.Integer(), nullable=False),
        sa.Column("bars_rejected", sa.Integer(), nullable=False),
        sa.Column("quote_used", sa.Boolean(), nullable=False),
        sa.Column("validation_report", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["ticker_id"], ["tickers.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_silver_build_run_ticker_id"), "silver_build_run", ["ticker_id"], unique=False)
    op.create_index(op.f("ix_silver_build_run_status"), "silver_build_run", ["status"], unique=False)

    op.create_table(
        "silver_build_run_bronze_ingestion_run",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("silver_build_run_id", sa.Integer(), nullable=False),
        sa.Column("bronze_ingestion_run_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["silver_build_run_id"], ["silver_build_run.id"]),
        sa.ForeignKeyConstraint(["bronze_ingestion_run_id"], ["bronze_ingestion_run.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "silver_build_run_id", "bronze_ingestion_run_id", name="uq_silver_build_run_bronze_run"
        ),
    )
    op.create_index(
        op.f("ix_silver_build_run_bronze_ingestion_run_silver_build_run_id"),
        "silver_build_run_bronze_ingestion_run",
        ["silver_build_run_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_silver_build_run_bronze_ingestion_run_bronze_ingestion_run_id"),
        "silver_build_run_bronze_ingestion_run",
        ["bronze_ingestion_run_id"],
        unique=False,
    )

    op.create_table(
        "silver_build_run_bar",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("silver_build_run_id", sa.Integer(), nullable=False),
        sa.Column("ticker_id", sa.Integer(), nullable=False),
        sa.Column("bar_date", sa.Date(), nullable=False),
        sa.Column("open", sa.Float(), nullable=False),
        sa.Column("high", sa.Float(), nullable=False),
        sa.Column("low", sa.Float(), nullable=False),
        sa.Column("close", sa.Float(), nullable=False),
        sa.Column("volume", sa.Integer(), nullable=False),
        sa.Column("data_quality_status", sa.String(length=20), nullable=False),
        sa.Column("remediation_actions", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(["silver_build_run_id"], ["silver_build_run.id"]),
        sa.ForeignKeyConstraint(["ticker_id"], ["tickers.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_silver_build_run_bar_silver_build_run_id"),
        "silver_build_run_bar",
        ["silver_build_run_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_silver_build_run_bar_ticker_id"), "silver_build_run_bar", ["ticker_id"], unique=False
    )
    op.create_index(
        op.f("ix_silver_build_run_bar_bar_date"), "silver_build_run_bar", ["bar_date"], unique=False
    )

    # --- silver_price_bar_rejected: build traceability ----------------------
    with op.batch_alter_table("silver_price_bar_rejected", schema=None) as batch_op:
        batch_op.add_column(sa.Column("silver_build_run_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_silver_price_bar_rejected_silver_build_run",
            "silver_build_run",
            ["silver_build_run_id"],
            ["id"],
        )
    op.create_index(
        op.f("ix_silver_price_bar_rejected_silver_build_run_id"),
        "silver_price_bar_rejected",
        ["silver_build_run_id"],
        unique=False,
    )

    # --- data migration: vocabulary rename, no schema change ----------------
    op.execute(
        "UPDATE silver_price_bar SET data_quality_status = 'clean_with_warnings' "
        "WHERE data_quality_status = 'flagged'"
    )

    # --- gold_calculation_run + dependency table ----------------------------
    op.create_table(
        "gold_calculation_run",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("ticker_id", sa.Integer(), nullable=False),
        sa.Column("product_name", sa.String(length=50), nullable=False),
        sa.Column("timeframe", sa.String(length=10), nullable=False, server_default="1d"),
        sa.Column("calculation_version", sa.String(length=20), nullable=False),
        sa.Column("configuration_version", sa.String(length=20), nullable=False),
        sa.Column("configuration_snapshot", sa.JSON(), nullable=False),
        sa.Column("silver_build_run_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="running"),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["ticker_id"], ["tickers.id"]),
        sa.ForeignKeyConstraint(["silver_build_run_id"], ["silver_build_run.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_gold_calculation_run_ticker_id"), "gold_calculation_run", ["ticker_id"], unique=False
    )
    op.create_index(
        op.f("ix_gold_calculation_run_product_name"),
        "gold_calculation_run",
        ["product_name"],
        unique=False,
    )
    op.create_index(
        op.f("ix_gold_calculation_run_silver_build_run_id"),
        "gold_calculation_run",
        ["silver_build_run_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_gold_calculation_run_status"), "gold_calculation_run", ["status"], unique=False
    )

    op.create_table(
        "gold_calculation_run_dependency",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("gold_calculation_run_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("symbol", sa.String(length=15), nullable=False),
        sa.Column("silver_build_run_id", sa.Integer(), nullable=True),
        sa.Column("silver_record_count", sa.Integer(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(["gold_calculation_run_id"], ["gold_calculation_run.id"]),
        sa.ForeignKeyConstraint(["silver_build_run_id"], ["silver_build_run.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_gold_calculation_run_dependency_gold_calculation_run_id"),
        "gold_calculation_run_dependency",
        ["gold_calculation_run_id"],
        unique=False,
    )

    # --- widen each gold_* table's identity ---------------------------------
    for table_name, current_uq_name, canonical_uq_name in _GOLD_TABLES:
        with op.batch_alter_table(table_name, schema=None) as batch_op:
            batch_op.add_column(
                sa.Column("timeframe", sa.String(length=10), nullable=False, server_default="1d")
            )
            batch_op.add_column(sa.Column("configuration_version", sa.String(length=20), nullable=False, server_default="unversioned"))
            batch_op.add_column(sa.Column("gold_calculation_run_id", sa.Integer(), nullable=True))
            batch_op.add_column(sa.Column("silver_build_run_id", sa.Integer(), nullable=True))
            batch_op.create_foreign_key(
                f"fk_{table_name}_gold_calculation_run",
                "gold_calculation_run",
                ["gold_calculation_run_id"],
                ["id"],
            )
            batch_op.create_foreign_key(
                f"fk_{table_name}_silver_build_run", "silver_build_run", ["silver_build_run_id"], ["id"]
            )
            batch_op.drop_constraint(current_uq_name, type_="unique")
            batch_op.create_unique_constraint(
                canonical_uq_name,
                [
                    "ticker_id",
                    "date",
                    "timeframe",
                    "calculation_version",
                    "configuration_version",
                    "silver_build_run_id",
                ],
            )


def downgrade() -> None:
    for table_name, current_uq_name, canonical_uq_name in reversed(_GOLD_TABLES):
        with op.batch_alter_table(table_name, schema=None) as batch_op:
            batch_op.drop_constraint(canonical_uq_name, type_="unique")
            # Restored under its original (pre-hardening) name - matches
            # what bd2ffb846c76 actually left in place for these four
            # tables (only gold_technical_snapshot ever had the canonical
            # name at the DB level).
            batch_op.create_unique_constraint(
                current_uq_name, ["ticker_id", "date", "calculation_version"]
            )
            batch_op.drop_constraint(f"fk_{table_name}_silver_build_run", type_="foreignkey")
            batch_op.drop_constraint(f"fk_{table_name}_gold_calculation_run", type_="foreignkey")
            batch_op.drop_column("silver_build_run_id")
            batch_op.drop_column("gold_calculation_run_id")
            batch_op.drop_column("configuration_version")
            batch_op.drop_column("timeframe")

    op.drop_index(
        op.f("ix_gold_calculation_run_dependency_gold_calculation_run_id"),
        table_name="gold_calculation_run_dependency",
    )
    op.drop_table("gold_calculation_run_dependency")

    op.drop_index(op.f("ix_gold_calculation_run_status"), table_name="gold_calculation_run")
    op.drop_index(
        op.f("ix_gold_calculation_run_silver_build_run_id"), table_name="gold_calculation_run"
    )
    op.drop_index(op.f("ix_gold_calculation_run_product_name"), table_name="gold_calculation_run")
    op.drop_index(op.f("ix_gold_calculation_run_ticker_id"), table_name="gold_calculation_run")
    op.drop_table("gold_calculation_run")

    op.execute(
        "UPDATE silver_price_bar SET data_quality_status = 'flagged' "
        "WHERE data_quality_status = 'clean_with_warnings'"
    )

    op.drop_index(
        op.f("ix_silver_price_bar_rejected_silver_build_run_id"),
        table_name="silver_price_bar_rejected",
    )
    with op.batch_alter_table("silver_price_bar_rejected", schema=None) as batch_op:
        batch_op.drop_constraint("fk_silver_price_bar_rejected_silver_build_run", type_="foreignkey")
        batch_op.drop_column("silver_build_run_id")

    op.drop_index(op.f("ix_silver_build_run_bar_bar_date"), table_name="silver_build_run_bar")
    op.drop_index(op.f("ix_silver_build_run_bar_ticker_id"), table_name="silver_build_run_bar")
    op.drop_index(
        op.f("ix_silver_build_run_bar_silver_build_run_id"), table_name="silver_build_run_bar"
    )
    op.drop_table("silver_build_run_bar")

    op.drop_index(
        op.f("ix_silver_build_run_bronze_ingestion_run_bronze_ingestion_run_id"),
        table_name="silver_build_run_bronze_ingestion_run",
    )
    op.drop_index(
        op.f("ix_silver_build_run_bronze_ingestion_run_silver_build_run_id"),
        table_name="silver_build_run_bronze_ingestion_run",
    )
    op.drop_table("silver_build_run_bronze_ingestion_run")

    op.drop_index(op.f("ix_silver_build_run_status"), table_name="silver_build_run")
    op.drop_index(op.f("ix_silver_build_run_ticker_id"), table_name="silver_build_run")
    op.drop_table("silver_build_run")

    op.drop_index(op.f("ix_bronze_market_quote_ticker_id"), table_name="bronze_market_quote")
    op.drop_index(
        op.f("ix_bronze_market_quote_ingestion_run_id"), table_name="bronze_market_quote"
    )
    op.drop_table("bronze_market_quote")

    op.drop_index(
        op.f("ix_bronze_ingestion_run_requested_symbol"), table_name="bronze_ingestion_run"
    )
    with op.batch_alter_table("bronze_ingestion_run", schema=None) as batch_op:
        batch_op.drop_column("provider_timezone")
        batch_op.drop_column("provider_adapter_version")
        batch_op.drop_column("request_params")
        batch_op.drop_column("requested_end")
        batch_op.drop_column("requested_start")
        batch_op.drop_column("requested_interval")
        batch_op.alter_column("requested_symbol", new_column_name="symbol")
    op.create_index(
        op.f("ix_bronze_ingestion_run_symbol"), "bronze_ingestion_run", ["symbol"], unique=False
    )
