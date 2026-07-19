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


class SilverRecordMixin:
    """The common columns every normalized Silver record carries (spec §14),
    so a downstream consumer can trace, validate, and reproduce any record
    uniformly regardless of domain. Concrete Silver tables inherit this and
    add their own domain-specific identity/value columns plus a `payload`.

    `stable_identifier` is the domain's stable key (symbol, FRED series id,
    CIK, exchange+session-date, ...) - never a value that can be reused or
    reassigned across entities. `normalization_version` is bumped when a
    normalizer's field-mapping changes, mirroring adapter/calculation
    versioning elsewhere."""

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    stable_identifier: Mapped[str] = mapped_column(String(100), index=True)
    provider: Mapped[str] = mapped_column(String(50))
    # The source's own record identifier (accession no., observation key, ...).
    source_record_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # When the datum became available at the source, distinct from when we
    # retrieved it and from its effective/observation time.
    source_available_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    effective_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    retrieved_at: Mapped[dt.datetime] = mapped_column(DateTime)
    bronze_ingestion_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("bronze_ingestion_run.id"), nullable=True
    )
    validation_status: Mapped[str] = mapped_column(String(20), default="clean")
    data_quality_warnings: Mapped[list | None] = mapped_column(JSON, nullable=True)
    normalization_version: Mapped[str] = mapped_column(String(20), default="1.0.0")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime)


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
    # Allowed values are the IngestionStatus set (catalystiq/providers/base.py):
    # running | succeeded | partial | failed | rate_limited | unavailable.
    # The single-provider-call price pipeline only lands on succeeded/failed
    # today; the rest exist for the network-backed adapters added later.
    status: Mapped[str] = mapped_column(String(20), default="running", index=True)
    bars_fetched: Mapped[int] = mapped_column(Integer, default=0)
    error_detail: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    # --- Generalized ingestion-run fields (spec §3) -------------------
    # Added additively so this one table serves every data domain, not just
    # market_price. All nullable: the existing price-bar path leaves them
    # unset and keeps writing requested_symbol/bars_fetched above. Network-
    # backed adapters (Phase 2+) populate these.
    #
    # `requested_identifier` is the domain-agnostic form of "what was
    # requested" (symbol, CIK, FRED/BLS series id, BEA table+line, ...);
    # `requested_symbol` stays the market-data specialization.
    requested_identifier: Mapped[str | None] = mapped_column(String(100), nullable=True)
    dataset: Mapped[str | None] = mapped_column(String(100), nullable=True)
    endpoint: Mapped[str | None] = mapped_column(String(255), nullable=True)
    data_classification: Mapped[str | None] = mapped_column(String(20), nullable=True)
    license_classification: Mapped[str | None] = mapped_column(String(30), nullable=True)
    # Provider's own response timestamp, and the original data-release
    # timestamp when the provider exposes one - kept distinct from
    # requested_at/completed_at (which are our clock), never conflated.
    response_timestamp: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    release_timestamp: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Domain-agnostic record count (bars_fetched is the price-bar alias).
    record_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rate_limit_info: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    # Normalized ProviderErrorCategory value; error_detail holds the
    # sanitized (secret-free) message.
    error_category: Mapped[str | None] = mapped_column(String(30), nullable=True)
    # Integrity + reference for the raw payload. `payload_reference` points
    # at immutable external storage when the payload is too large to inline.
    payload_checksum: Mapped[str | None] = mapped_column(String(64), nullable=True)
    payload_reference: Mapped[str | None] = mapped_column(String(500), nullable=True)


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


class BronzeRawDocument(Base):
    """Generic append-only store for a raw provider payload from any
    document/record source (SEC filings & facts, FRED series/observations,
    the NYSE schedule snapshot, ...). Complements the domain-specific Bronze
    tables above (BronzeMarketPriceBar/Quote): those predate this and stay
    as-is; new network/document domains land here instead of getting a
    bespoke Bronze table each.

    Append-only like all Bronze - a re-ingest writes a new row (new
    ingestion run), never overwrites a prior payload. `payload_checksum`
    lets a Silver build detect an unchanged document and skip reprocessing."""

    __tablename__ = "bronze_raw_document"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ingestion_run_id: Mapped[int] = mapped_column(
        ForeignKey("bronze_ingestion_run.id"), index=True
    )
    domain: Mapped[str] = mapped_column(String(50), index=True)
    # The requested/source identifier this document is for (symbol, CIK,
    # series id, "NYSE", ...).
    source_identifier: Mapped[str] = mapped_column(String(100), index=True)
    document_type: Mapped[str] = mapped_column(String(50))
    payload: Mapped[dict] = mapped_column(JSON)
    payload_checksum: Mapped[str] = mapped_column(String(64), index=True)
    source_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    source_timestamp: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    fetched_at: Mapped[dt.datetime] = mapped_column(DateTime)


# --- Silver: validated, deduplicated, normalized. Reads only from Bronze.
# Unique on (ticker, date) - Silver IS the current-best-known clean view,
# so upserting here on reprocessing is correct (unlike Bronze).

class SilverMarketSession(Base, SilverRecordMixin):
    """Normalized exchange trading session (§10). The market-calendar Silver
    product used to decide the latest completed session, whether an intraday
    candle is complete, and staleness - a real calendar, not a flat 24h
    rule. Idempotent: upserted on (exchange, session_date)."""

    __tablename__ = "silver_market_session"
    __table_args__ = (
        UniqueConstraint("exchange", "session_date", name="uq_silver_market_session"),
    )

    exchange: Mapped[str] = mapped_column(String(15), index=True)
    session_date: Mapped[dt.date] = mapped_column(index=True)
    open_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    close_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    timezone: Mapped[str] = mapped_column(String(40))
    early_close: Mapped[bool] = mapped_column(default=False)
    holiday_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    calendar_version: Mapped[str] = mapped_column(String(30))


class SilverMacroSeries(Base, SilverRecordMixin):
    """Normalized macro series metadata (§9's macro_series). Idempotent on
    (provider, series_id)."""

    __tablename__ = "silver_macro_series"
    __table_args__ = (
        UniqueConstraint("provider", "series_id", name="uq_silver_macro_series"),
    )

    series_id: Mapped[str] = mapped_column(String(50), index=True)
    title: Mapped[str | None] = mapped_column(String(500), nullable=True)
    frequency: Mapped[str | None] = mapped_column(String(30), nullable=True)
    units: Mapped[str | None] = mapped_column(String(100), nullable=True)
    seasonal_adjustment: Mapped[str | None] = mapped_column(String(50), nullable=True)
    observation_start: Mapped[dt.date | None] = mapped_column(nullable=True)
    observation_end: Mapped[dt.date | None] = mapped_column(nullable=True)


class SilverMacroObservation(Base, SilverRecordMixin):
    """Normalized macro observation with point-in-time vintage (§7). Unique on
    (provider, series_id, observation_date, realtime_start) so every vintage
    of a given observation date coexists - a revised value is a NEW row, the
    originally-known value is never overwritten. `value` is None for a
    missing observation, never fabricated."""

    __tablename__ = "silver_macro_observation"
    __table_args__ = (
        UniqueConstraint(
            "provider",
            "series_id",
            "observation_date",
            "realtime_start",
            name="uq_silver_macro_observation_vintage",
        ),
    )

    series_id: Mapped[str] = mapped_column(String(50), index=True)
    observation_date: Mapped[dt.date] = mapped_column(index=True)
    value: Mapped[float | None] = mapped_column(Float, nullable=True)
    realtime_start: Mapped[dt.date | None] = mapped_column(nullable=True)
    realtime_end: Mapped[dt.date | None] = mapped_column(nullable=True)
    units: Mapped[str | None] = mapped_column(String(100), nullable=True)
    frequency: Mapped[str | None] = mapped_column(String(30), nullable=True)
    seasonal_adjustment: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # Provider-specific source fields (BLS footnotes/period/preliminary, ...),
    # so the shared observation model doesn't drop a source's own metadata.
    source_fields: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class SilverEconomicRelease(Base, SilverRecordMixin):
    """Normalized economic release (§11's economic_release). Keeps scheduled
    release date and actual publication timestamp as distinct concepts (§7).
    Idempotent on (provider, release_id, scheduled_date)."""

    __tablename__ = "silver_economic_release"
    __table_args__ = (
        UniqueConstraint(
            "provider", "release_id", "scheduled_date", name="uq_silver_economic_release"
        ),
    )

    release_id: Mapped[str] = mapped_column(String(30), index=True)
    name: Mapped[str | None] = mapped_column(String(300), nullable=True)
    scheduled_date: Mapped[dt.date | None] = mapped_column(nullable=True)
    actual_published_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    press_release: Mapped[bool | None] = mapped_column(nullable=True)
    link: Mapped[str | None] = mapped_column(String(500), nullable=True)


class SilverBeaValue(Base, SilverRecordMixin):
    """Normalized BEA table value (§9). Idempotent on
    (provider, dataset, table_name, line_number, time_period, frequency).
    Nominal/real/annualized/SA values are distinguished by their table +
    unit, never merged."""

    __tablename__ = "silver_bea_value"
    __table_args__ = (
        UniqueConstraint(
            "provider", "dataset", "table_name", "line_number", "time_period", "frequency",
            name="uq_silver_bea_value",
        ),
    )

    dataset: Mapped[str] = mapped_column(String(30), index=True)
    table_name: Mapped[str] = mapped_column(String(40), index=True)
    line_number: Mapped[str | None] = mapped_column(String(20), nullable=True)
    line_description: Mapped[str | None] = mapped_column(String(300), nullable=True)
    series_code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    time_period: Mapped[str] = mapped_column(String(20), index=True)
    frequency: Mapped[str | None] = mapped_column(String(10), nullable=True)
    value: Mapped[float | None] = mapped_column(Float, nullable=True)
    unit: Mapped[str | None] = mapped_column(String(60), nullable=True)
    scale: Mapped[str | None] = mapped_column(String(20), nullable=True)


class SilverSecurityIdentifier(Base, SilverRecordMixin):
    """Ticker <-> CIK mapping (§6). Idempotent on (provider, cik). `symbol`
    is indexed but not the identity - tickers can change or be reused, so the
    CIK is the stable key (spec §12 principle)."""

    __tablename__ = "silver_security_identifier"
    __table_args__ = (
        UniqueConstraint("provider", "cik", name="uq_silver_security_identifier"),
    )

    cik: Mapped[str] = mapped_column(String(10), index=True)
    symbol: Mapped[str] = mapped_column(String(15), index=True)
    name: Mapped[str | None] = mapped_column(String(300), nullable=True)


class SilverCompanyFiling(Base, SilverRecordMixin):
    """A company filing's metadata (§6). Idempotent on
    (provider, accession_number) - the accession number is SEC's stable id
    for a filing."""

    __tablename__ = "silver_company_filing"
    __table_args__ = (
        UniqueConstraint("provider", "accession_number", name="uq_silver_company_filing"),
    )

    cik: Mapped[str] = mapped_column(String(10), index=True)
    symbol: Mapped[str | None] = mapped_column(String(15), nullable=True)
    form: Mapped[str] = mapped_column(String(20), index=True)
    accession_number: Mapped[str] = mapped_column(String(30), index=True)
    filing_date: Mapped[dt.date | None] = mapped_column(nullable=True, index=True)
    acceptance_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    report_date: Mapped[dt.date | None] = mapped_column(nullable=True)
    primary_document: Mapped[str | None] = mapped_column(String(255), nullable=True)
    primary_doc_description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_amendment: Mapped[bool] = mapped_column(default=False)
    source_url: Mapped[str | None] = mapped_column(String(500), nullable=True)


class SilverCompanyFact(Base, SilverRecordMixin):
    """One normalized XBRL company fact (§6) - also serves as the financial-
    statement fact (an XBRL fact IS a statement-line fact), so it isn't
    duplicated into a second table.

    Identity includes the accession number, so an amended filing's value
    lands as a NEW row rather than overwriting the originally-filed value
    (§6); `is_amendment` flags it and `filing_date` orders vintages. The
    active value for a (concept, unit, period) is the row with the latest
    filing_date - see fundamentals_pipeline.get_active_facts()."""

    __tablename__ = "silver_company_fact"
    __table_args__ = (
        UniqueConstraint(
            "provider",
            "cik",
            "accession_number",
            "taxonomy",
            "concept",
            "unit",
            "period_start",
            "period_end",
            name="uq_silver_company_fact",
        ),
    )

    cik: Mapped[str] = mapped_column(String(10), index=True)
    taxonomy: Mapped[str] = mapped_column(String(30))
    concept: Mapped[str] = mapped_column(String(120), index=True)
    unit: Mapped[str] = mapped_column(String(30))
    value: Mapped[float | None] = mapped_column(Float, nullable=True)
    fiscal_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fiscal_period: Mapped[str | None] = mapped_column(String(10), nullable=True)
    period_start: Mapped[dt.date | None] = mapped_column(nullable=True)
    period_end: Mapped[dt.date | None] = mapped_column(nullable=True)
    form: Mapped[str | None] = mapped_column(String(20), nullable=True)
    filing_date: Mapped[dt.date | None] = mapped_column(nullable=True)
    accession_number: Mapped[str | None] = mapped_column(String(30), nullable=True)
    is_amendment: Mapped[bool] = mapped_column(default=False)
    frame: Mapped[str | None] = mapped_column(String(50), nullable=True)


class SilverMaterialEvent(Base, SilverRecordMixin):
    """An 8-K material event (§6). Idempotent on (provider, accession_number)."""

    __tablename__ = "silver_material_event"
    __table_args__ = (
        UniqueConstraint("provider", "accession_number", name="uq_silver_material_event"),
    )

    cik: Mapped[str] = mapped_column(String(10), index=True)
    symbol: Mapped[str | None] = mapped_column(String(15), nullable=True)
    accession_number: Mapped[str] = mapped_column(String(30), index=True)
    form: Mapped[str] = mapped_column(String(20))
    filing_date: Mapped[dt.date | None] = mapped_column(nullable=True, index=True)
    acceptance_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    items: Mapped[list | None] = mapped_column(JSON, nullable=True)
    is_amendment: Mapped[bool] = mapped_column(default=False)
    source_url: Mapped[str | None] = mapped_column(String(500), nullable=True)


class SilverSecurityMaster(Base, SilverRecordMixin):
    """Security master / symbol directory (§12, §14 #1). Keyed on a stable
    internal security id, NOT the ticker alone (tickers can change or be
    reused). Idempotent on (provider, internal_security_id)."""

    __tablename__ = "silver_security_master"
    __table_args__ = (
        UniqueConstraint(
            "provider", "internal_security_id", name="uq_silver_security_master"
        ),
    )

    internal_security_id: Mapped[str] = mapped_column(String(60), index=True)
    symbol: Mapped[str] = mapped_column(String(15), index=True)
    name: Mapped[str | None] = mapped_column(String(300), nullable=True)
    exchange: Mapped[str | None] = mapped_column(String(20), nullable=True)
    listing_market: Mapped[str | None] = mapped_column(String(20), nullable=True)
    etf: Mapped[bool | None] = mapped_column(nullable=True)
    test_issue: Mapped[bool | None] = mapped_column(nullable=True)
    is_active: Mapped[bool] = mapped_column(default=True)


class SilverShortSaleVolume(Base, SilverRecordMixin):
    """Daily short-sale volume (§11) - a SEPARATE dataset from short interest.
    Idempotent on (provider, symbol, trade_date, reporting_facility,
    file_version); file_version is in the key so a corrected file is
    preserved alongside the original."""

    __tablename__ = "silver_short_sale_volume"
    __table_args__ = (
        UniqueConstraint(
            "provider", "symbol", "trade_date", "reporting_facility", "file_version",
            name="uq_silver_short_sale_volume",
        ),
    )

    symbol: Mapped[str] = mapped_column(String(15), index=True)
    trade_date: Mapped[dt.date] = mapped_column(index=True)
    short_volume: Mapped[int | None] = mapped_column(Integer, nullable=True)
    short_exempt_volume: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_volume: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reporting_facility: Mapped[str | None] = mapped_column(String(20), nullable=True)
    file_version: Mapped[str] = mapped_column(String(20), default="original")


class SilverShortInterest(Base, SilverRecordMixin):
    """Semi-monthly equity short interest (§11) - a SEPARATE dataset from
    daily short-sale volume; the two are never conflated. Idempotent on
    (provider, symbol, settlement_date, file_version)."""

    __tablename__ = "silver_short_interest"
    __table_args__ = (
        UniqueConstraint(
            "provider", "symbol", "settlement_date", "file_version",
            name="uq_silver_short_interest",
        ),
    )

    symbol: Mapped[str] = mapped_column(String(15), index=True)
    settlement_date: Mapped[dt.date] = mapped_column(index=True)
    publication_date: Mapped[dt.date | None] = mapped_column(nullable=True)
    short_interest_quantity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    previous_short_interest_quantity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    average_daily_volume: Mapped[float | None] = mapped_column(Float, nullable=True)
    days_to_cover: Mapped[float | None] = mapped_column(Float, nullable=True)
    file_version: Mapped[str] = mapped_column(String(20), default="original")


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


class ProviderComparison(Base):
    """A cross-provider validation result (§5, §16): the primary and
    secondary providers' values for the same field, their difference, and
    which was selected and why. Values are recorded, never averaged; an
    out-of-tolerance row IS the data-quality warning."""

    __tablename__ = "provider_comparison"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    domain: Mapped[str] = mapped_column(String(30), index=True)
    symbol: Mapped[str] = mapped_column(String(15), index=True)
    field: Mapped[str] = mapped_column(String(30))  # quote_price | close
    as_of_date: Mapped[dt.date | None] = mapped_column(nullable=True)
    primary_provider: Mapped[str] = mapped_column(String(30))
    primary_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    primary_timestamp: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    secondary_provider: Mapped[str] = mapped_column(String(30))
    secondary_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    secondary_timestamp: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    absolute_diff: Mapped[float | None] = mapped_column(Float, nullable=True)
    relative_diff_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    tolerance_pct: Mapped[float] = mapped_column(Float)
    within_tolerance: Mapped[bool] = mapped_column(default=True, index=True)
    selected_provider: Mapped[str] = mapped_column(String(30))
    selected_reason: Mapped[str] = mapped_column(String(300))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, index=True)


class OrderConfirmationToken(Base):
    """A single-use, short-lived confirmation token bound to exact order
    details (§13). Submission consumes it (sets used_at); a replay or a
    parameter change is rejected. See catalystiq/orders.py."""

    __tablename__ = "order_confirmation_token"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    jti: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    fingerprint: Mapped[str] = mapped_column(String(1000))
    account_id: Mapped[str] = mapped_column(String(100))
    mode: Mapped[str] = mapped_column(String(10))  # paper | live
    estimated_max_loss: Mapped[float | None] = mapped_column(Float, nullable=True)
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime)
    used_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
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


class MLModelArtifact(Base):
    """Registry of every candidate and approved ML model artifact (ML build
    spec). ONLY rows with approval_status='approved' may serve user-facing
    predictions - the inference layer enforces that. An artifact records its
    full training/validation/calibration/holdout window boundaries, the
    schema/target/data versions it was built against, the code commit, its
    hyperparameters and its evaluation + calibration metrics, so any served
    prediction is fully reproducible and auditable.

    Nothing writes approval_status='approved' automatically - approval is a
    deliberate, human, out-of-band action. A row whose training_data_version
    is marked synthetic must never be approved for user-facing use."""

    __tablename__ = "ml_model_artifact"
    __table_args__ = (
        UniqueConstraint("model_name", "model_version", name="uq_ml_model_name_version"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    model_name: Mapped[str] = mapped_column(String(120), index=True)
    model_version: Mapped[str] = mapped_column(String(40))
    # model_1 | model_2 | model_3 | model_4 | model_5
    model_family: Mapped[str] = mapped_column(String(30), index=True)
    horizon_days: Mapped[int] = mapped_column(Integer, index=True)
    trade_direction: Mapped[str] = mapped_column(String(10), index=True)  # long | short

    training_start: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    training_end: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    validation_start: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    validation_end: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    calibration_start: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    calibration_end: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    holdout_start: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    holdout_end: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)

    feature_schema_version: Mapped[str] = mapped_column(String(40))
    target_definition_version: Mapped[str] = mapped_column(String(40))
    training_data_version: Mapped[str] = mapped_column(String(80))
    code_commit: Mapped[str | None] = mapped_column(String(80), nullable=True)

    hyperparameters: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    evaluation_metrics: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    calibration_metrics: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # candidate | approved | rejected | archived. Defaults to candidate - an
    # artifact is NEVER born approved.
    approval_status: Mapped[str] = mapped_column(String(20), default="candidate", index=True)
    # True when built on synthetic/demo data (unit tests). Such a row must
    # never be approved for user-facing predictions.
    is_synthetic: Mapped[bool] = mapped_column(default=False)
    # Optional path/URI to the serialized model object (out-of-band storage).
    artifact_uri: Mapped[str | None] = mapped_column(String(500), nullable=True)
    notes: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime)
