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
    symbol: Mapped[str] = mapped_column(String(15), index=True)
    provider: Mapped[str] = mapped_column(String(50))
    requested_at: Mapped[dt.datetime] = mapped_column(DateTime)
    started_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="running", index=True)
    bars_fetched: Mapped[int] = mapped_column(Integer, default=0)
    error_detail: Mapped[str | None] = mapped_column(String(1000), nullable=True)


class BronzeMarketPriceBar(Base):
    __tablename__ = "bronze_market_price_bar"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ingestion_run_id: Mapped[int] = mapped_column(ForeignKey("bronze_ingestion_run.id"), index=True)
    ticker_id: Mapped[int] = mapped_column(ForeignKey("tickers.id"), index=True)
    source_symbol: Mapped[str] = mapped_column(String(15))
    bar_date: Mapped[dt.date] = mapped_column(index=True)
    raw_payload: Mapped[dict] = mapped_column(JSON)
    source_timestamp: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
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
    bar_date: Mapped[dt.date] = mapped_column(index=True)
    rejection_reason: Mapped[str] = mapped_column(String(1000))
    rejected_at: Mapped[dt.datetime] = mapped_column(DateTime)


# --- Gold: curated analytical products, read only from Silver. Uniform
# shape across all five products - id/ticker/date/calculation_version/
# payload (the product's own Pydantic response, serialized)/lineage
# columns. See catalystiq/pipelines/market_price_pipeline.py's
# build_gold(). `Record` suffix avoids colliding with the Pydantic
# response classes of the same name in catalystiq/schemas/*.py.

class TechnicalSnapshotRecord(Base):
    __tablename__ = "gold_technical_snapshot"
    __table_args__ = (
        UniqueConstraint("ticker_id", "date", "calculation_version", name="uq_gold_technical_snapshot"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticker_id: Mapped[int] = mapped_column(ForeignKey("tickers.id"), index=True)
    date: Mapped[dt.date] = mapped_column(index=True)
    calculation_version: Mapped[str] = mapped_column(String(20))
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
            "ticker_id", "date", "calculation_version", name="uq_gold_market_structure_snapshot"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticker_id: Mapped[int] = mapped_column(ForeignKey("tickers.id"), index=True)
    date: Mapped[dt.date] = mapped_column(index=True)
    calculation_version: Mapped[str] = mapped_column(String(20))
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
        UniqueConstraint("ticker_id", "date", "calculation_version", name="uq_gold_risk_snapshot"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticker_id: Mapped[int] = mapped_column(ForeignKey("tickers.id"), index=True)
    date: Mapped[dt.date] = mapped_column(index=True)
    calculation_version: Mapped[str] = mapped_column(String(20))
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
            "ticker_id", "date", "calculation_version", name="uq_gold_volume_liquidity_snapshot"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticker_id: Mapped[int] = mapped_column(ForeignKey("tickers.id"), index=True)
    date: Mapped[dt.date] = mapped_column(index=True)
    calculation_version: Mapped[str] = mapped_column(String(20))
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
            "ticker_id", "date", "calculation_version", name="uq_gold_market_context_snapshot"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticker_id: Mapped[int] = mapped_column(ForeignKey("tickers.id"), index=True)
    date: Mapped[dt.date] = mapped_column(index=True)
    calculation_version: Mapped[str] = mapped_column(String(20))
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
