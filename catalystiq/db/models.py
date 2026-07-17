"""ORM models matching the build spec's schema sketch (§7).

Table shapes follow §7 directly; types/constraints are filled in since the
spec only sketches column names. `behavioral_events` and
`reinforcement_stats` back the FBA engine (§3); the rest back the core
analytical engine (§2).
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from catalystiq.db.base import Base


class Ticker(Base):
    __tablename__ = "tickers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(15), unique=True, index=True)
    sector: Mapped[str | None] = mapped_column(String(100), nullable=True)
    industry: Mapped[str | None] = mapped_column(String(100), nullable=True)

    price_history: Mapped[list["SilverPriceBar"]] = relationship(
        back_populates="ticker", cascade="all, delete-orphan"
    )


# --- Bronze: source-aligned, minimally-transformed raw data. Append-only -
# a routine re-ingest never overwrites a prior run's rows (no unique
# constraint on ticker+date here; that's Silver's job). See
# catalystiq/pipelines/market_price_pipeline.py's ingest_bronze().

class BronzeIngestionRun(Base):
    __tablename__ = "bronze_ingestion_run"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    domain: Mapped[str] = mapped_column(String(50), index=True)
    requested_symbol: Mapped[str] = mapped_column(String(15), index=True)
    requested_interval: Mapped[str] = mapped_column(String(10), default="1d")
    requested_start: Mapped[dt.date | None] = mapped_column(nullable=True)
    requested_end: Mapped[dt.date | None] = mapped_column(nullable=True)
    # Full kwargs passed to the provider call, for exact reproducibility
    # beyond what the individual columns above capture.
    request_params: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    provider: Mapped[str] = mapped_column(String(50))
    provider_adapter_version: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # Left null: yfinance's returned index can carry exchange tz info, but
    # this adapter discards it at parse time today (documented limitation,
    # not fabricated data - see YahooFinanceProvider.get_ohlcv()).
    provider_timezone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    requested_at: Mapped[dt.datetime] = mapped_column(DateTime)
    started_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    # partial: reserved for a future multi-call ingestion path - the
    # current single-provider-call design can only ever land on succeeded
    # or failed (see market_price_pipeline.py's ingest_bronze() docstring).
    status: Mapped[str] = mapped_column(String(20), default="running", index=True)
    bars_fetched: Mapped[int] = mapped_column(Integer, default=0)
    error_detail: Mapped[str | None] = mapped_column(String(1000), nullable=True)


class BronzeMarketPriceBar(Base):
    """`raw_payload` is source-aligned, not byte-for-byte raw: the
    MarketDataProvider abstraction (catalystiq/providers/market_data.py)
    already normalizes the underlying yfinance response into the
    provider-agnostic `OHLCVBar` shape before Bronze ever sees it, so this
    is "as the provider adapter returned it," not the original Yahoo JSON.
    """

    __tablename__ = "bronze_market_price_bar"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ingestion_run_id: Mapped[int] = mapped_column(ForeignKey("bronze_ingestion_run.id"), index=True)
    ticker_id: Mapped[int] = mapped_column(ForeignKey("tickers.id"), index=True)
    source_symbol: Mapped[str] = mapped_column(String(15))
    bar_date: Mapped[dt.date] = mapped_column(index=True)
    raw_payload: Mapped[dict] = mapped_column(JSON)
    # Left null: the daily OHLCVBar shape has no per-bar timestamp beyond
    # its trading date - not fabricated from the date itself.
    source_timestamp: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    ingested_at: Mapped[dt.datetime] = mapped_column(DateTime)


class BronzeMarketQuote(Base):
    """A live/previous-close quote fetched alongside an ingestion run, kept
    for traceability instead of being fetched-then-discarded. Persisted by
    ingest_bronze_quote() from both the explicit ingest endpoint and
    ensure_fresh()'s on-demand path."""

    __tablename__ = "bronze_market_quote"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ingestion_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("bronze_ingestion_run.id"), nullable=True, index=True
    )
    ticker_id: Mapped[int] = mapped_column(ForeignKey("tickers.id"), index=True)
    source_symbol: Mapped[str] = mapped_column(String(15))
    price: Mapped[float] = mapped_column(Float)
    previous_close: Mapped[float | None] = mapped_column(Float, nullable=True)
    quote_as_of: Mapped[dt.datetime] = mapped_column(DateTime)
    raw_payload: Mapped[dict] = mapped_column(JSON)
    ingested_at: Mapped[dt.datetime] = mapped_column(DateTime)


# --- Silver: validated, deduplicated, normalized. Reads only from Bronze.
# Unique on (ticker, date) - Silver IS the current-best-known clean view,
# so upserting here on reprocessing is correct (unlike Bronze).

class SilverPriceBar(Base):
    __tablename__ = "silver_price_bar"
    __table_args__ = (UniqueConstraint("ticker_id", "date", name="uq_silver_price_bar_ticker_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticker_id: Mapped[int] = mapped_column(ForeignKey("tickers.id"), index=True)
    date: Mapped[dt.date] = mapped_column(index=True)
    open: Mapped[float] = mapped_column(Float)
    high: Mapped[float] = mapped_column(Float)
    low: Mapped[float] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    volume: Mapped[int] = mapped_column(Integer)
    source_bronze_ingestion_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("bronze_ingestion_run.id"), nullable=True
    )
    data_quality_status: Mapped[str] = mapped_column(String(20), default="clean")
    remediation_actions: Mapped[list | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime)

    ticker: Mapped["Ticker"] = relationship(back_populates="price_history")


class SilverPriceBarRejected(Base):
    """Quarantine for Bronze rows that failed the Data Validation Layer
    (§2.9) during build_silver() - rejected, not silently dropped."""

    __tablename__ = "silver_price_bar_rejected"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticker_id: Mapped[int] = mapped_column(ForeignKey("tickers.id"), index=True)
    source_bronze_market_price_bar_id: Mapped[int] = mapped_column(
        ForeignKey("bronze_market_price_bar.id")
    )
    silver_build_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("silver_build_run.id"), nullable=True, index=True
    )
    bar_date: Mapped[dt.date] = mapped_column(index=True)
    rejection_reason: Mapped[str] = mapped_column(String(1000))
    rejected_at: Mapped[dt.datetime] = mapped_column(DateTime)


# --- Silver build runs: an immutable audit trail of every build_silver()
# call, independent of the live (mutable, upsert-in-place) SilverPriceBar
# table above. SilverBuildRunBar snapshots exactly what a build wrote so a
# Gold calculation pinned to a specific build stays reproducible even after
# later builds change the live silver_price_bar rows.

class SilverBuildRun(Base):
    __tablename__ = "silver_build_run"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticker_id: Mapped[int] = mapped_column(ForeignKey("tickers.id"), index=True)
    started_at: Mapped[dt.datetime] = mapped_column(DateTime)
    completed_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    # partial: some bars in the source Bronze rows failed to parse (e.g. a
    # malformed raw_payload) - the rest of the build still commits.
    status: Mapped[str] = mapped_column(String(20), default="running", index=True)
    bars_upserted: Mapped[int] = mapped_column(Integer, default=0)
    bars_rejected: Mapped[int] = mapped_column(Integer, default=0)
    quote_used: Mapped[bool] = mapped_column(default=False)
    validation_report: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime)


class SilverBuildRunBronzeIngestionRun(Base):
    """Association: every distinct Bronze ingestion run that contributed at
    least one bar to a given Silver build - a build may span multiple runs
    (e.g. a historical backfill plus subsequent incremental ingests)."""

    __tablename__ = "silver_build_run_bronze_ingestion_run"
    __table_args__ = (
        UniqueConstraint(
            "silver_build_run_id", "bronze_ingestion_run_id", name="uq_silver_build_run_bronze_run"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    silver_build_run_id: Mapped[int] = mapped_column(ForeignKey("silver_build_run.id"), index=True)
    bronze_ingestion_run_id: Mapped[int] = mapped_column(
        ForeignKey("bronze_ingestion_run.id"), index=True
    )


class SilverBuildRunBar(Base):
    """Immutable snapshot of exactly what one Silver build wrote for one
    bar - decoupled from the live SilverPriceBar table (which is upserted
    in place on every reprocessing) so a Gold calculation that references
    this build's id can always be reproduced exactly, even after a later
    build changes the current-state Silver row for the same date."""

    __tablename__ = "silver_build_run_bar"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    silver_build_run_id: Mapped[int] = mapped_column(ForeignKey("silver_build_run.id"), index=True)
    ticker_id: Mapped[int] = mapped_column(ForeignKey("tickers.id"), index=True)
    bar_date: Mapped[dt.date] = mapped_column(index=True)
    open: Mapped[float] = mapped_column(Float)
    high: Mapped[float] = mapped_column(Float)
    low: Mapped[float] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    volume: Mapped[int] = mapped_column(Integer)
    data_quality_status: Mapped[str] = mapped_column(String(20))
    remediation_actions: Mapped[list | None] = mapped_column(JSON, nullable=True)


# --- Gold: curated analytical products, read only from Silver. Uniform
# shape across all five products - id/ticker/date/calculation_version/
# configuration_version/timeframe/payload (the product's own Pydantic
# response, serialized)/lineage columns. See
# catalystiq/pipelines/market_price_pipeline.py's build_gold_*().
# `Record` suffix avoids colliding with the Pydantic response classes of
# the same name in catalystiq/schemas/*.py.
#
# Full run-level lineage: GoldCalculationRun -> SilverBuildRun -> Bronze
# runs (via the association table above). `bronze_ingestion_run_id` on
# each gold_* table below is a denormalized "primary symbol's latest run"
# convenience pointer, not the sole lineage source - the authoritative
# multi-run, multi-symbol chain lives in GoldCalculationRun,
# GoldCalculationRunDependency, and SilverBuildRunBronzeIngestionRun.

class GoldCalculationRun(Base):
    __tablename__ = "gold_calculation_run"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticker_id: Mapped[int] = mapped_column(ForeignKey("tickers.id"), index=True)
    product_name: Mapped[str] = mapped_column(String(50), index=True)
    timeframe: Mapped[str] = mapped_column(String(10), default="1d")
    calculation_version: Mapped[str] = mapped_column(String(20))
    configuration_version: Mapped[str] = mapped_column(String(20))
    configuration_snapshot: Mapped[dict] = mapped_column(JSON)
    silver_build_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("silver_build_run.id"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(String(20), default="running", index=True)
    started_at: Mapped[dt.datetime] = mapped_column(DateTime)
    completed_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime)
    # Set by the synchronous anomaly sanity check right after this run's
    # Gold row is persisted (NaN/inf/implausible-magnitude bounds - no
    # reference library involved). The async reference-validation loop
    # (catalystiq/validation/reference/scheduler.py) processes every
    # flagged run before sampling others.
    flagged_for_reference_check: Mapped[bool] = mapped_column(default=False, index=True)
    reference_checked_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)


class GoldCalculationRunDependency(Base):
    """Every upstream symbol a Gold calculation actually read, beyond the
    primary requested symbol - e.g. Risk's benchmark, Market Context's
    market/sector ETFs. Makes multi-symbol lineage explicit and queryable
    instead of silently folded into (or omitted from) the primary symbol's
    lineage."""

    __tablename__ = "gold_calculation_run_dependency"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    gold_calculation_run_id: Mapped[int] = mapped_column(
        ForeignKey("gold_calculation_run.id"), index=True
    )
    role: Mapped[str] = mapped_column(String(20))  # primary | benchmark | market | sector
    symbol: Mapped[str] = mapped_column(String(15))
    silver_build_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("silver_build_run.id"), nullable=True
    )
    silver_record_count: Mapped[int] = mapped_column(Integer, default=0)


class GoldReferenceCheck(Base):
    """Audit trail for the reference-calculation adapter
    (catalystiq/validation/reference/): one row per indicator per check
    run. Symbol/timestamps/silver build id/calculation version/
    configuration version are all derivable via `gold_calculation_run_id`
    (the existing source of truth for that lineage) rather than duplicated
    here. A `status="fail"` row is never accompanied by silently
    overwriting the Gold output - see market_price_pipeline.py's
    quarantine handling, which sets the affected gold_* row's
    data_quality_status to "quarantined" instead."""

    __tablename__ = "gold_reference_check"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    gold_calculation_run_id: Mapped[int] = mapped_column(
        ForeignKey("gold_calculation_run.id"), index=True
    )
    ticker_id: Mapped[int] = mapped_column(ForeignKey("tickers.id"), index=True)
    indicator_name: Mapped[str] = mapped_column(String(100), index=True)
    reference_source: Mapped[str] = mapped_column(String(30))  # talib | tradingview_formula | independent_stats
    reference_library: Mapped[str] = mapped_column(String(50))
    reference_library_version: Mapped[str] = mapped_column(String(20))
    parameters: Mapped[dict] = mapped_column(JSON)
    expected_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    actual_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    absolute_diff: Mapped[float | None] = mapped_column(Float, nullable=True)
    relative_diff: Mapped[float | None] = mapped_column(Float, nullable=True)
    tolerance_abs: Mapped[float | None] = mapped_column(Float, nullable=True)
    tolerance_rel: Mapped[float | None] = mapped_column(Float, nullable=True)
    warmup_bars_excluded: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), index=True)  # pass | fail | not_applicable
    discrepancy_reason: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    checked_at: Mapped[dt.datetime] = mapped_column(DateTime)


class TechnicalSnapshotRecord(Base):
    __tablename__ = "gold_technical_snapshot"
    __table_args__ = (
        UniqueConstraint(
            "ticker_id",
            "date",
            "timeframe",
            "calculation_version",
            "configuration_version",
            "silver_build_run_id",
            name="uq_gold_technical_snapshot",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticker_id: Mapped[int] = mapped_column(ForeignKey("tickers.id"), index=True)
    date: Mapped[dt.date] = mapped_column(index=True)
    timeframe: Mapped[str] = mapped_column(String(10), default="1d")
    calculation_version: Mapped[str] = mapped_column(String(20))
    configuration_version: Mapped[str] = mapped_column(String(20))
    gold_calculation_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("gold_calculation_run.id"), nullable=True
    )
    silver_build_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("silver_build_run.id"), nullable=True
    )
    payload: Mapped[dict] = mapped_column(JSON)
    data_quality_status: Mapped[str] = mapped_column(String(20), default="available")
    silver_record_count: Mapped[int] = mapped_column(Integer, default=0)
    silver_date_range_start: Mapped[dt.date | None] = mapped_column(nullable=True)
    silver_date_range_end: Mapped[dt.date | None] = mapped_column(nullable=True)
    bronze_ingestion_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("bronze_ingestion_run.id"), nullable=True
    )
    source_provider: Mapped[str] = mapped_column(String(50))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime)


class MarketStructureSnapshotRecord(Base):
    """Persisted snapshot of the Market Structure data product (§6)."""

    __tablename__ = "gold_market_structure_snapshot"
    __table_args__ = (
        UniqueConstraint(
            "ticker_id",
            "date",
            "timeframe",
            "calculation_version",
            "configuration_version",
            "silver_build_run_id",
            name="uq_gold_market_structure_snapshot",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticker_id: Mapped[int] = mapped_column(ForeignKey("tickers.id"), index=True)
    date: Mapped[dt.date] = mapped_column(index=True)
    timeframe: Mapped[str] = mapped_column(String(10), default="1d")
    calculation_version: Mapped[str] = mapped_column(String(20))
    configuration_version: Mapped[str] = mapped_column(String(20))
    gold_calculation_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("gold_calculation_run.id"), nullable=True
    )
    silver_build_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("silver_build_run.id"), nullable=True
    )
    payload: Mapped[dict] = mapped_column(JSON)
    data_quality_status: Mapped[str] = mapped_column(String(20), default="available")
    silver_record_count: Mapped[int] = mapped_column(Integer, default=0)
    silver_date_range_start: Mapped[dt.date | None] = mapped_column(nullable=True)
    silver_date_range_end: Mapped[dt.date | None] = mapped_column(nullable=True)
    bronze_ingestion_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("bronze_ingestion_run.id"), nullable=True
    )
    source_provider: Mapped[str] = mapped_column(String(50))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime)


class RiskSnapshotRecord(Base):
    """Persisted snapshot of the Volatility & Risk data product (§7)."""

    __tablename__ = "gold_risk_snapshot"
    __table_args__ = (
        UniqueConstraint(
            "ticker_id",
            "date",
            "timeframe",
            "calculation_version",
            "configuration_version",
            "silver_build_run_id",
            name="uq_gold_risk_snapshot",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticker_id: Mapped[int] = mapped_column(ForeignKey("tickers.id"), index=True)
    date: Mapped[dt.date] = mapped_column(index=True)
    timeframe: Mapped[str] = mapped_column(String(10), default="1d")
    calculation_version: Mapped[str] = mapped_column(String(20))
    configuration_version: Mapped[str] = mapped_column(String(20))
    gold_calculation_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("gold_calculation_run.id"), nullable=True
    )
    silver_build_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("silver_build_run.id"), nullable=True
    )
    benchmark_symbol: Mapped[str | None] = mapped_column(String(15), nullable=True)
    payload: Mapped[dict] = mapped_column(JSON)
    data_quality_status: Mapped[str] = mapped_column(String(20), default="available")
    silver_record_count: Mapped[int] = mapped_column(Integer, default=0)
    silver_date_range_start: Mapped[dt.date | None] = mapped_column(nullable=True)
    silver_date_range_end: Mapped[dt.date | None] = mapped_column(nullable=True)
    bronze_ingestion_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("bronze_ingestion_run.id"), nullable=True
    )
    source_provider: Mapped[str] = mapped_column(String(50))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime)


class VolumeLiquiditySnapshotRecord(Base):
    """Persisted snapshot of the Volume & Liquidity data product (§8)."""

    __tablename__ = "gold_volume_liquidity_snapshot"
    __table_args__ = (
        UniqueConstraint(
            "ticker_id",
            "date",
            "timeframe",
            "calculation_version",
            "configuration_version",
            "silver_build_run_id",
            name="uq_gold_volume_liquidity_snapshot",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticker_id: Mapped[int] = mapped_column(ForeignKey("tickers.id"), index=True)
    date: Mapped[dt.date] = mapped_column(index=True)
    timeframe: Mapped[str] = mapped_column(String(10), default="1d")
    calculation_version: Mapped[str] = mapped_column(String(20))
    configuration_version: Mapped[str] = mapped_column(String(20))
    gold_calculation_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("gold_calculation_run.id"), nullable=True
    )
    silver_build_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("silver_build_run.id"), nullable=True
    )
    payload: Mapped[dict] = mapped_column(JSON)
    data_quality_status: Mapped[str] = mapped_column(String(20), default="available")
    silver_record_count: Mapped[int] = mapped_column(Integer, default=0)
    silver_date_range_start: Mapped[dt.date | None] = mapped_column(nullable=True)
    silver_date_range_end: Mapped[dt.date | None] = mapped_column(nullable=True)
    bronze_ingestion_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("bronze_ingestion_run.id"), nullable=True
    )
    source_provider: Mapped[str] = mapped_column(String(50))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime)


class MarketContextSnapshotRecord(Base):
    """Persisted snapshot of the Market & Sector Context data product (§14.1)."""

    __tablename__ = "gold_market_context_snapshot"
    __table_args__ = (
        UniqueConstraint(
            "ticker_id",
            "date",
            "timeframe",
            "calculation_version",
            "configuration_version",
            "silver_build_run_id",
            name="uq_gold_market_context_snapshot",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticker_id: Mapped[int] = mapped_column(ForeignKey("tickers.id"), index=True)
    date: Mapped[dt.date] = mapped_column(index=True)
    timeframe: Mapped[str] = mapped_column(String(10), default="1d")
    calculation_version: Mapped[str] = mapped_column(String(20))
    configuration_version: Mapped[str] = mapped_column(String(20))
    gold_calculation_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("gold_calculation_run.id"), nullable=True
    )
    silver_build_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("silver_build_run.id"), nullable=True
    )
    market_symbol: Mapped[str | None] = mapped_column(String(15), nullable=True)
    sector_symbol: Mapped[str | None] = mapped_column(String(15), nullable=True)
    payload: Mapped[dict] = mapped_column(JSON)
    data_quality_status: Mapped[str] = mapped_column(String(20), default="available")
    silver_record_count: Mapped[int] = mapped_column(Integer, default=0)
    silver_date_range_start: Mapped[dt.date | None] = mapped_column(nullable=True)
    silver_date_range_end: Mapped[dt.date | None] = mapped_column(nullable=True)
    bronze_ingestion_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("bronze_ingestion_run.id"), nullable=True
    )
    source_provider: Mapped[str] = mapped_column(String(50))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime)


class OptionsSnapshot(Base):
    __tablename__ = "options_snapshots"
    __table_args__ = (UniqueConstraint("ticker_id", "date", name="uq_options_snapshot"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticker_id: Mapped[int] = mapped_column(ForeignKey("tickers.id"), index=True)
    date: Mapped[dt.date] = mapped_column(index=True)
    iv: Mapped[float | None] = mapped_column(Float, nullable=True)
    iv_rank: Mapped[float | None] = mapped_column(Float, nullable=True)
    put_call_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_pain: Mapped[float | None] = mapped_column(Float, nullable=True)
    expected_move: Mapped[float | None] = mapped_column(Float, nullable=True)


class NewsEvent(Base):
    __tablename__ = "news_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticker_id: Mapped[int] = mapped_column(ForeignKey("tickers.id"), index=True)
    date: Mapped[dt.datetime] = mapped_column(DateTime, index=True)
    headline: Mapped[str] = mapped_column(String(500))
    source_url: Mapped[str] = mapped_column(String(1000))
    category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    sentiment_score: Mapped[float | None] = mapped_column(Float, nullable=True)


class BehavioralEvent(Base):
    """One detected ABC (Antecedent -> Behavior -> Consequence) instance (§3.1)."""

    __tablename__ = "behavioral_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticker_id: Mapped[int] = mapped_column(ForeignKey("tickers.id"), index=True)
    date: Mapped[dt.datetime] = mapped_column(DateTime, index=True)
    antecedent_tags: Mapped[list[str]] = mapped_column(JSON)
    behavior_tag: Mapped[str] = mapped_column(String(100), index=True)
    consequence_tag: Mapped[str | None] = mapped_column(String(100), nullable=True)
    magnitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    detection_rule_id: Mapped[str] = mapped_column(String(100))


class ReinforcementStat(Base):
    """Empirical reinforcement schedule for an antecedent/behavior/consequence triple (§3.2.3).

    Scoped to either a ticker or a sector via `scope_type`/`scope_id`, since
    the spec allows lookups at the ticker or sector/peer-group level.
    """

    __tablename__ = "reinforcement_stats"
    __table_args__ = (
        UniqueConstraint(
            "scope_type",
            "scope_id",
            "antecedent_tag",
            "behavior_tag",
            "consequence_tag",
            name="uq_reinforcement_stat",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scope_type: Mapped[str] = mapped_column(String(10))  # "ticker" | "sector"
    scope_id: Mapped[int] = mapped_column(Integer, index=True)
    antecedent_tag: Mapped[str] = mapped_column(String(100), index=True)
    behavior_tag: Mapped[str] = mapped_column(String(100), index=True)
    consequence_tag: Mapped[str] = mapped_column(String(100))
    occurrence_count: Mapped[int] = mapped_column(Integer)
    follow_through_rate: Mapped[float] = mapped_column(Float)


class ScheduledOrder(Base):
    """A trade order queued for future submission (§1.1 Execution Zone).

    Executed by an in-process background loop (see catalystiq/main.py's
    lifespan) that polls for due, pending rows and submits them through the
    configured BrokerProvider. That loop only runs while this process is
    alive - see the README for that limitation.
    """

    __tablename__ = "scheduled_orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(15), index=True)
    order_json: Mapped[dict] = mapped_column(JSON)
    scheduled_at: Mapped[dt.datetime] = mapped_column(DateTime, index=True)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    broker_order_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_detail: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime)


class Report(Base):
    __tablename__ = "reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticker_id: Mapped[int] = mapped_column(ForeignKey("tickers.id"), index=True)
    timeframe: Mapped[str] = mapped_column(String(50))
    generated_at: Mapped[dt.datetime] = mapped_column(DateTime, index=True)
    rating: Mapped[str] = mapped_column(String(20))
    confidence: Mapped[float] = mapped_column(Float)
    bullish_pct: Mapped[float] = mapped_column(Float)
    neutral_pct: Mapped[float] = mapped_column(Float)
    bearish_pct: Mapped[float] = mapped_column(Float)
    report_json: Mapped[dict] = mapped_column(JSON)
