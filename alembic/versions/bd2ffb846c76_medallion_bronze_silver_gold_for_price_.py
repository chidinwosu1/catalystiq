"""medallion (bronze/silver/gold) for the price-bar domain

Revision ID: bd2ffb846c76
Revises: 19dbdcbfedc4
Create Date: 2026-07-17 18:00:00.000000

Retrofits Bronze -> Silver -> Gold onto the price-bar domain
(catalystiq/pipelines/market_price_pipeline.py):

  - new bronze_ingestion_run / bronze_market_price_bar tables
    (append-only raw ingestion audit trail).
  - price_history -> silver_price_bar: renamed in place, plus new
    data-quality/lineage columns. Nothing ever wrote to price_history
    outside the ingest endpoint, and the ingest endpoint upserted the same
    columns this migration preserves, so existing rows carry forward with
    data_quality_status defaulted to "clean" and remediation_actions null.
  - indicator_snapshots -> gold_technical_snapshot: dropped and recreated
    rather than altered in place. The old per-(ticker, date,
    indicator_name) row shape is structurally incompatible with the new
    uniform one-row-per-(ticker, date, calculation_version) Gold shape used
    by all five products, and nothing has ever written to this table (it
    predates any code path that inserts into it).
  - market_structure_snapshots / risk_snapshots / volume_liquidity_snapshots
    / market_context_snapshots -> gold_* names: same uniform payload shape
    already, so these are a straight rename + added lineage columns.
    Nothing has written to these either (added this session, never wired
    to a writer until this pipeline).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "bd2ffb846c76"
down_revision: Union[str, Sequence[str], None] = "19dbdcbfedc4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_GOLD_LINEAGE_COLUMNS = [
    sa.Column("silver_record_count", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("silver_date_range_start", sa.Date(), nullable=True),
    sa.Column("silver_date_range_end", sa.Date(), nullable=True),
    sa.Column("bronze_ingestion_run_id", sa.Integer(), nullable=True),
    sa.Column("source_provider", sa.String(length=50), nullable=False, server_default="yahoo"),
]

_GOLD_TABLE_RENAMES = [
    ("market_structure_snapshots", "gold_market_structure_snapshot", "market_structure_snapshot"),
    ("risk_snapshots", "gold_risk_snapshot", "risk_snapshot"),
    ("volume_liquidity_snapshots", "gold_volume_liquidity_snapshot", "volume_liquidity_snapshot"),
    ("market_context_snapshots", "gold_market_context_snapshot", "market_context_snapshot"),
]


def upgrade() -> None:
    # --- Bronze: new tables ------------------------------------------------
    op.create_table(
        "bronze_ingestion_run",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("domain", sa.String(length=50), nullable=False),
        sa.Column("symbol", sa.String(length=15), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("requested_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("bars_fetched", sa.Integer(), nullable=False),
        sa.Column("error_detail", sa.String(length=1000), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_bronze_ingestion_run_domain"), "bronze_ingestion_run", ["domain"], unique=False
    )
    op.create_index(
        op.f("ix_bronze_ingestion_run_symbol"), "bronze_ingestion_run", ["symbol"], unique=False
    )
    op.create_index(
        op.f("ix_bronze_ingestion_run_status"), "bronze_ingestion_run", ["status"], unique=False
    )

    op.create_table(
        "bronze_market_price_bar",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("ingestion_run_id", sa.Integer(), nullable=False),
        sa.Column("ticker_id", sa.Integer(), nullable=False),
        sa.Column("source_symbol", sa.String(length=15), nullable=False),
        sa.Column("bar_date", sa.Date(), nullable=False),
        sa.Column("raw_payload", sa.JSON(), nullable=False),
        sa.Column("source_timestamp", sa.DateTime(), nullable=True),
        sa.Column("ingested_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["ingestion_run_id"], ["bronze_ingestion_run.id"]),
        sa.ForeignKeyConstraint(["ticker_id"], ["tickers.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_bronze_market_price_bar_ingestion_run_id"),
        "bronze_market_price_bar",
        ["ingestion_run_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_bronze_market_price_bar_ticker_id"),
        "bronze_market_price_bar",
        ["ticker_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_bronze_market_price_bar_bar_date"),
        "bronze_market_price_bar",
        ["bar_date"],
        unique=False,
    )

    # --- Silver: rename price_history in place, add quality/lineage cols ---
    with op.batch_alter_table("price_history", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("source_bronze_ingestion_run_id", sa.Integer(), nullable=True)
        )
        batch_op.add_column(
            sa.Column(
                "data_quality_status",
                sa.String(length=20),
                nullable=False,
                server_default="clean",
            )
        )
        batch_op.add_column(sa.Column("remediation_actions", sa.JSON(), nullable=True))
        batch_op.add_column(
            sa.Column(
                "created_at",
                sa.DateTime(),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            )
        )
        batch_op.add_column(
            sa.Column(
                "updated_at",
                sa.DateTime(),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            )
        )
        batch_op.drop_constraint("uq_price_history_ticker_date", type_="unique")
        batch_op.create_unique_constraint(
            "uq_silver_price_bar_ticker_date", ["ticker_id", "date"]
        )
        batch_op.create_foreign_key(
            "fk_silver_price_bar_bronze_ingestion_run",
            "bronze_ingestion_run",
            ["source_bronze_ingestion_run_id"],
            ["id"],
        )

    op.drop_index(op.f("ix_price_history_date"), table_name="price_history")
    op.drop_index(op.f("ix_price_history_ticker_id"), table_name="price_history")
    op.rename_table("price_history", "silver_price_bar")
    op.create_index(
        op.f("ix_silver_price_bar_date"), "silver_price_bar", ["date"], unique=False
    )
    op.create_index(
        op.f("ix_silver_price_bar_ticker_id"), "silver_price_bar", ["ticker_id"], unique=False
    )

    op.create_table(
        "silver_price_bar_rejected",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("ticker_id", sa.Integer(), nullable=False),
        sa.Column("source_bronze_market_price_bar_id", sa.Integer(), nullable=False),
        sa.Column("bar_date", sa.Date(), nullable=False),
        sa.Column("rejection_reason", sa.String(length=1000), nullable=False),
        sa.Column("rejected_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["ticker_id"], ["tickers.id"]),
        sa.ForeignKeyConstraint(
            ["source_bronze_market_price_bar_id"], ["bronze_market_price_bar.id"]
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_silver_price_bar_rejected_ticker_id"),
        "silver_price_bar_rejected",
        ["ticker_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_silver_price_bar_rejected_bar_date"),
        "silver_price_bar_rejected",
        ["bar_date"],
        unique=False,
    )

    # --- Gold: indicator_snapshots -> gold_technical_snapshot (incompatible
    # shape - drop and recreate; nothing has ever written to this table) ---
    op.drop_index(op.f("ix_indicator_snapshots_ticker_id"), table_name="indicator_snapshots")
    op.drop_index(
        op.f("ix_indicator_snapshots_indicator_name"), table_name="indicator_snapshots"
    )
    op.drop_index(op.f("ix_indicator_snapshots_date"), table_name="indicator_snapshots")
    op.drop_table("indicator_snapshots")

    op.create_table(
        "gold_technical_snapshot",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("ticker_id", sa.Integer(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("calculation_version", sa.String(length=20), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column(
            "data_quality_status", sa.String(length=20), nullable=False, server_default="available"
        ),
        *_GOLD_LINEAGE_COLUMNS,
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["ticker_id"], ["tickers.id"]),
        sa.ForeignKeyConstraint(["bronze_ingestion_run_id"], ["bronze_ingestion_run.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "ticker_id", "date", "calculation_version", name="uq_gold_technical_snapshot"
        ),
    )
    op.create_index(
        op.f("ix_gold_technical_snapshot_date"), "gold_technical_snapshot", ["date"], unique=False
    )
    op.create_index(
        op.f("ix_gold_technical_snapshot_ticker_id"),
        "gold_technical_snapshot",
        ["ticker_id"],
        unique=False,
    )

    # --- Gold: rename the 4 already-uniform-shaped snapshot tables, add
    # lineage columns ---
    for old_name, new_name, short in _GOLD_TABLE_RENAMES:
        with op.batch_alter_table(old_name, schema=None) as batch_op:
            for col in _GOLD_LINEAGE_COLUMNS:
                batch_op.add_column(col.copy())
            batch_op.create_foreign_key(
                f"fk_{new_name}_bronze_ingestion_run",
                "bronze_ingestion_run",
                ["bronze_ingestion_run_id"],
                ["id"],
            )

        op.drop_index(op.f(f"ix_{old_name}_date"), table_name=old_name)
        op.drop_index(op.f(f"ix_{old_name}_ticker_id"), table_name=old_name)
        op.rename_table(old_name, new_name)
        op.create_index(op.f(f"ix_{new_name}_date"), new_name, ["date"], unique=False)
        op.create_index(op.f(f"ix_{new_name}_ticker_id"), new_name, ["ticker_id"], unique=False)


def downgrade() -> None:
    for old_name, new_name, short in reversed(_GOLD_TABLE_RENAMES):
        op.drop_index(op.f(f"ix_{new_name}_ticker_id"), table_name=new_name)
        op.drop_index(op.f(f"ix_{new_name}_date"), table_name=new_name)
        op.rename_table(new_name, old_name)
        op.create_index(op.f(f"ix_{old_name}_date"), old_name, ["date"], unique=False)
        op.create_index(op.f(f"ix_{old_name}_ticker_id"), old_name, ["ticker_id"], unique=False)
        with op.batch_alter_table(old_name, schema=None) as batch_op:
            batch_op.drop_constraint(f"fk_{new_name}_bronze_ingestion_run", type_="foreignkey")
            for col in reversed(_GOLD_LINEAGE_COLUMNS):
                batch_op.drop_column(col.name)

    op.drop_index(op.f("ix_gold_technical_snapshot_ticker_id"), table_name="gold_technical_snapshot")
    op.drop_index(op.f("ix_gold_technical_snapshot_date"), table_name="gold_technical_snapshot")
    op.drop_table("gold_technical_snapshot")

    op.create_table(
        "indicator_snapshots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("ticker_id", sa.Integer(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("indicator_name", sa.String(length=100), nullable=False),
        sa.Column("value", sa.Float(), nullable=False),
        sa.Column("percentile_5y", sa.Float(), nullable=True),
        sa.ForeignKeyConstraint(["ticker_id"], ["tickers.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("ticker_id", "date", "indicator_name", name="uq_indicator_snapshot"),
    )
    op.create_index(
        op.f("ix_indicator_snapshots_date"), "indicator_snapshots", ["date"], unique=False
    )
    op.create_index(
        op.f("ix_indicator_snapshots_indicator_name"),
        "indicator_snapshots",
        ["indicator_name"],
        unique=False,
    )
    op.create_index(
        op.f("ix_indicator_snapshots_ticker_id"), "indicator_snapshots", ["ticker_id"], unique=False
    )

    op.drop_index(op.f("ix_silver_price_bar_rejected_bar_date"), table_name="silver_price_bar_rejected")
    op.drop_index(
        op.f("ix_silver_price_bar_rejected_ticker_id"), table_name="silver_price_bar_rejected"
    )
    op.drop_table("silver_price_bar_rejected")

    op.drop_index(op.f("ix_silver_price_bar_ticker_id"), table_name="silver_price_bar")
    op.drop_index(op.f("ix_silver_price_bar_date"), table_name="silver_price_bar")
    op.rename_table("silver_price_bar", "price_history")
    op.create_index(op.f("ix_price_history_date"), "price_history", ["date"], unique=False)
    op.create_index(
        op.f("ix_price_history_ticker_id"), "price_history", ["ticker_id"], unique=False
    )

    with op.batch_alter_table("price_history", schema=None) as batch_op:
        batch_op.drop_constraint("fk_silver_price_bar_bronze_ingestion_run", type_="foreignkey")
        batch_op.drop_constraint("uq_silver_price_bar_ticker_date", type_="unique")
        batch_op.create_unique_constraint(
            "uq_price_history_ticker_date", ["ticker_id", "date"]
        )
        batch_op.drop_column("updated_at")
        batch_op.drop_column("created_at")
        batch_op.drop_column("remediation_actions")
        batch_op.drop_column("data_quality_status")
        batch_op.drop_column("source_bronze_ingestion_run_id")

    op.drop_index(
        op.f("ix_bronze_market_price_bar_bar_date"), table_name="bronze_market_price_bar"
    )
    op.drop_index(
        op.f("ix_bronze_market_price_bar_ticker_id"), table_name="bronze_market_price_bar"
    )
    op.drop_index(
        op.f("ix_bronze_market_price_bar_ingestion_run_id"), table_name="bronze_market_price_bar"
    )
    op.drop_table("bronze_market_price_bar")

    op.drop_index(op.f("ix_bronze_ingestion_run_status"), table_name="bronze_ingestion_run")
    op.drop_index(op.f("ix_bronze_ingestion_run_symbol"), table_name="bronze_ingestion_run")
    op.drop_index(op.f("ix_bronze_ingestion_run_domain"), table_name="bronze_ingestion_run")
    op.drop_table("bronze_ingestion_run")
