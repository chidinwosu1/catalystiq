"""Bronze -> Silver -> Gold pipeline for the price-bar domain.

    Providers -> Bronze -> Silver -> Gold -> API/UI

This is the one domain with a real, working ingestion path today
(catalystiq/providers/market_data.py's MarketDataProvider.get_ohlcv()).
Other domains (fundamentals, news, macro, options) have no ingestion
pipeline at all yet, so there's no Bronze/Silver to build for them -
retrofitting a medallion flow onto data that's never persisted would be
inventing scope, not converting existing scope.

"Raw" in Bronze here means "as returned by MarketDataProvider.get_ohlcv()"
- the provider abstraction itself already normalizes a few field names out
of the underlying yfinance response (the original build spec's provider-
abstraction design, §1.1, predates this medallion request). Going further
back to literal un-normalized Yahoo JSON would mean bypassing that
abstraction entirely, a separate, larger question outside this pipeline's
scope.

Stage contracts:
  ingest_bronze() - fetches from the provider, writes Bronze rows only.
    Never touches Silver. Each call is a new, additive ingestion run - it
    never overwrites a prior run's Bronze rows ("avoid overwriting source
    history during routine ingestion").
  build_silver() - reads ONLY from Bronze (never calls the provider itself;
    an optional `live_quote` may be passed in by the caller from an
    approved real-time adapter for the cross-check, per the spec's "Bronze
    or approved real-time provider adapters" allowance), runs the existing
    Data Validation Layer (catalystiq/validation/data_quality.py), upserts
    passing bars into Silver (idempotent - keyed on ticker+date), and
    quarantines bars that fail a per-record check into
    SilverPriceBarRejected rather than silently dropping them.
  build_gold_*() - read ONLY from Silver via get_silver_bars() below
    (never call the provider), call the existing pure compute functions in
    catalystiq/analysis/*.py unchanged, attach a Lineage block, persist a
    row into the relevant gold_* table, and return the Pydantic response.
  ensure_fresh() - the one place a router-triggered flow is allowed to
    touch the provider: runs ingest_bronze() + build_silver() if Silver
    has no data (or it's stale) for the symbol. Routers call this before
    build_gold_*(); the Gold functions themselves never call it.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy.orm import Session

from catalystiq.analysis.indicators import compute_technical_snapshot
from catalystiq.analysis.market_context import compute_market_context_snapshot
from catalystiq.analysis.market_structure import compute_market_structure_snapshot
from catalystiq.analysis.risk import compute_risk_snapshot
from catalystiq.analysis.volume_liquidity import compute_volume_liquidity_snapshot
from catalystiq.db import models
from catalystiq.providers.market_data import MarketDataError, MarketDataProvider
from catalystiq.schemas.analysis import Lineage, TechnicalSnapshot
from catalystiq.schemas.market_context import MarketContextSnapshot
from catalystiq.schemas.market_data import OHLCVBar, Quote
from catalystiq.schemas.market_structure import MarketStructureSnapshot
from catalystiq.schemas.risk import RiskSnapshot
from catalystiq.schemas.validation import DataQualityIssueType, DataQualityReport
from catalystiq.schemas.volume_liquidity import VolumeLiquiditySnapshot
from catalystiq.validation.data_quality import validate_price_history

DOMAIN = "market_price"
DEFAULT_CALCULATION_VERSION = "1.0.0"


def _get_or_create_ticker(db: Session, symbol: str) -> models.Ticker:
    symbol = symbol.upper()
    ticker = db.query(models.Ticker).filter_by(symbol=symbol).one_or_none()
    if ticker is None:
        ticker = models.Ticker(symbol=symbol)
        db.add(ticker)
        db.flush()
    return ticker


# --- Bronze ------------------------------------------------------------


def ingest_bronze(
    symbol: str, days: int, provider: MarketDataProvider, db: Session
) -> models.BronzeIngestionRun:
    """Fetches raw OHLCV from the provider and writes Bronze rows. Never
    reads or writes Silver. Safe to call repeatedly - each call is a new
    run, never overwriting a previous run's rows."""
    symbol = symbol.upper()
    ticker = _get_or_create_ticker(db, symbol)
    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)

    run = models.BronzeIngestionRun(
        domain=DOMAIN,
        symbol=symbol,
        provider=type(provider).__name__,
        requested_at=now,
        started_at=now,
        status="running",
        bars_fetched=0,
    )
    db.add(run)
    db.flush()

    try:
        bars = provider.get_ohlcv(symbol, start=dt.date.today() - dt.timedelta(days=days))
    except MarketDataError as exc:
        run.status = "failed"
        run.completed_at = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
        run.error_detail = str(exc)
        db.commit()
        raise

    ingested_at = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    for bar in bars:
        db.add(
            models.BronzeMarketPriceBar(
                ingestion_run_id=run.id,
                ticker_id=ticker.id,
                source_symbol=symbol,
                bar_date=bar.date,
                raw_payload=bar.model_dump(mode="json"),
                source_timestamp=None,
                ingested_at=ingested_at,
            )
        )

    run.status = "succeeded"
    run.bars_fetched = len(bars)
    run.completed_at = ingested_at
    db.commit()
    db.refresh(run)
    return run


# --- Silver --------------------------------------------------------------


class SilverBuildResult:
    def __init__(
        self, upserted: int, rejected: int, ticker_id: int, report: DataQualityReport | None = None
    ):
        self.upserted = upserted
        self.rejected = rejected
        self.ticker_id = ticker_id
        self.report = report


# Issue types that disqualify the specific bar they're attached to, rather
# than just flagging an otherwise-valid bar (e.g. an abnormal gap is real,
# unusual price action - not invalid data - so it's flagged, not rejected).
_REJECTING_ISSUE_TYPES = {DataQualityIssueType.INVALID_OHLC_RELATIONSHIP}


def build_silver(
    symbol: str,
    db: Session,
    ingestion_run: models.BronzeIngestionRun | None = None,
    live_quote: Quote | None = None,
) -> SilverBuildResult:
    """Reads ONLY from Bronze (a specific run, or the latest successful run
    for the symbol) - never calls the provider itself. `live_quote`, if
    given, must already have been fetched by the caller from an approved
    real-time adapter (the spec explicitly allows Silver to read from
    "Bronze or approved real-time provider adapters") - this function never
    fabricates one from Bronze data. Runs the existing Data Validation
    Layer, upserts passing bars into SilverPriceBar (idempotent - keyed on
    ticker+date), and quarantines bars with a per-bar-invalidating issue
    into SilverPriceBarRejected instead of silently dropping them."""
    symbol = symbol.upper()
    ticker = _get_or_create_ticker(db, symbol)

    if ingestion_run is None:
        ingestion_run = (
            db.query(models.BronzeIngestionRun)
            .filter_by(domain=DOMAIN, symbol=symbol, status="succeeded")
            .order_by(models.BronzeIngestionRun.id.desc())
            .first()
        )
    if ingestion_run is None:
        return SilverBuildResult(upserted=0, rejected=0, ticker_id=ticker.id)

    bronze_rows = (
        db.query(models.BronzeMarketPriceBar)
        .filter_by(ingestion_run_id=ingestion_run.id, ticker_id=ticker.id)
        .order_by(models.BronzeMarketPriceBar.bar_date)
        .all()
    )
    raw_bars = [OHLCVBar(**row.raw_payload) for row in bronze_rows]
    bronze_by_date = {row.bar_date: row for row in bronze_rows}

    cleaned_bars, report = validate_price_history(symbol, raw_bars, live_quote=live_quote)

    issues_by_date: dict[dt.date, list] = {}
    for issue in report.issues:
        if issue.date is not None:
            issues_by_date.setdefault(issue.date, []).append(issue)

    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    upserted = 0
    rejected = 0

    for bar in cleaned_bars:
        bar_issues = issues_by_date.get(bar.date, [])
        rejecting = [i for i in bar_issues if i.type in _REJECTING_ISSUE_TYPES]

        if rejecting:
            bronze_row = bronze_by_date.get(bar.date)
            db.add(
                models.SilverPriceBarRejected(
                    ticker_id=ticker.id,
                    source_bronze_market_price_bar_id=(
                        bronze_row.id if bronze_row else bronze_rows[0].id
                    ),
                    bar_date=bar.date,
                    rejection_reason="; ".join(i.detail for i in rejecting),
                    rejected_at=now,
                )
            )
            rejected += 1
            continue

        status = "flagged" if bar_issues else "clean"
        remediation = [i.model_dump(mode="json") for i in bar_issues] or None

        existing = (
            db.query(models.SilverPriceBar)
            .filter_by(ticker_id=ticker.id, date=bar.date)
            .one_or_none()
        )
        if existing is None:
            db.add(
                models.SilverPriceBar(
                    ticker_id=ticker.id,
                    date=bar.date,
                    open=bar.open,
                    high=bar.high,
                    low=bar.low,
                    close=bar.close,
                    volume=bar.volume,
                    source_bronze_ingestion_run_id=ingestion_run.id,
                    data_quality_status=status,
                    remediation_actions=remediation,
                    created_at=now,
                    updated_at=now,
                )
            )
        else:
            existing.open = bar.open
            existing.high = bar.high
            existing.low = bar.low
            existing.close = bar.close
            existing.volume = bar.volume
            existing.source_bronze_ingestion_run_id = ingestion_run.id
            existing.data_quality_status = status
            existing.remediation_actions = remediation
            existing.updated_at = now
        upserted += 1

    db.commit()
    return SilverBuildResult(
        upserted=upserted, rejected=rejected, ticker_id=ticker.id, report=report
    )


def ensure_fresh(
    symbol: str,
    provider: MarketDataProvider,
    db: Session,
    days: int = 365 * 5,
    max_age_hours: int = 24,
) -> None:
    """Runs ingest_bronze() + build_silver() if Silver has no data for this
    symbol, or its newest row is older than max_age_hours. No-ops
    otherwise. The only place in this module that touches the provider on
    a router-triggered, on-demand basis - build_gold_*() below never do."""
    symbol = symbol.upper()
    ticker = db.query(models.Ticker).filter_by(symbol=symbol).one_or_none()

    if ticker is not None:
        latest = (
            db.query(models.SilverPriceBar)
            .filter_by(ticker_id=ticker.id)
            .order_by(models.SilverPriceBar.updated_at.desc())
            .first()
        )
        if latest is not None:
            age = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) - latest.updated_at
            if age <= dt.timedelta(hours=max_age_hours):
                return

    run = ingest_bronze(symbol, days, provider, db)
    build_silver(symbol, db, ingestion_run=run)


# --- Gold read boundary ----------------------------------------------------


def get_silver_bars(symbol: str, db: Session) -> list[OHLCVBar]:
    """The ONLY data-read path a Gold compute function may use. Reads
    SilverPriceBar rows for `symbol` and converts them back to
    list[OHLCVBar] - the same shape the analysis/*.py compute functions
    already take, so none of them needed to change to become Gold
    products."""
    symbol = symbol.upper()
    ticker = db.query(models.Ticker).filter_by(symbol=symbol).one_or_none()
    if ticker is None:
        return []

    rows = (
        db.query(models.SilverPriceBar)
        .filter_by(ticker_id=ticker.id)
        .order_by(models.SilverPriceBar.date)
        .all()
    )
    return [
        OHLCVBar(date=r.date, open=r.open, high=r.high, low=r.low, close=r.close, volume=r.volume)
        for r in rows
    ]


def _build_lineage(
    symbol: str, db: Session, calculation_version: str, provider_name: str
) -> Lineage:
    symbol = symbol.upper()
    ticker = db.query(models.Ticker).filter_by(symbol=symbol).one_or_none()
    now = dt.datetime.now(dt.timezone.utc)
    if ticker is None:
        return Lineage(
            calculation_version=calculation_version,
            silver_record_count=0,
            source_provider=provider_name,
            calculated_at=now,
        )

    rows = db.query(models.SilverPriceBar).filter_by(ticker_id=ticker.id).all()
    run_ids = [r.source_bronze_ingestion_run_id for r in rows if r.source_bronze_ingestion_run_id]
    dates = [r.date for r in rows]
    return Lineage(
        calculation_version=calculation_version,
        silver_record_count=len(rows),
        silver_date_range_start=min(dates) if dates else None,
        silver_date_range_end=max(dates) if dates else None,
        bronze_ingestion_run_id=max(run_ids) if run_ids else None,
        source_provider=provider_name,
        calculated_at=now,
    )


def _persist_gold(
    db: Session,
    record_cls,
    symbol: str,
    lineage: Lineage,
    payload: dict,
    extra_fields: dict | None = None,
) -> None:
    ticker = _get_or_create_ticker(db, symbol)
    as_of_date = lineage.silver_date_range_end or dt.date.today()
    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)

    fields = {
        "payload": payload,
        "data_quality_status": "available",
        "silver_record_count": lineage.silver_record_count,
        "silver_date_range_start": lineage.silver_date_range_start,
        "silver_date_range_end": lineage.silver_date_range_end,
        "bronze_ingestion_run_id": lineage.bronze_ingestion_run_id,
        "source_provider": lineage.source_provider,
        **(extra_fields or {}),
    }

    existing = (
        db.query(record_cls)
        .filter_by(
            ticker_id=ticker.id, date=as_of_date, calculation_version=lineage.calculation_version
        )
        .one_or_none()
    )
    if existing is None:
        db.add(
            record_cls(
                ticker_id=ticker.id,
                date=as_of_date,
                calculation_version=lineage.calculation_version,
                created_at=now,
                **fields,
            )
        )
    else:
        for key, value in fields.items():
            setattr(existing, key, value)
    db.commit()


# --- Gold products -----------------------------------------------------


def build_gold_technical(
    symbol: str,
    db: Session,
    calculation_version: str = DEFAULT_CALCULATION_VERSION,
    provider_name: str = "yahoo",
) -> TechnicalSnapshot:
    bars = get_silver_bars(symbol, db)
    snapshot = compute_technical_snapshot(symbol, bars)
    lineage = _build_lineage(symbol, db, calculation_version, provider_name)
    snapshot.lineage = lineage
    _persist_gold(
        db, models.TechnicalSnapshotRecord, symbol, lineage, snapshot.model_dump(mode="json")
    )
    return snapshot


def build_gold_market_structure(
    symbol: str,
    db: Session,
    calculation_version: str = DEFAULT_CALCULATION_VERSION,
    provider_name: str = "yahoo",
) -> MarketStructureSnapshot:
    bars = get_silver_bars(symbol, db)
    snapshot = compute_market_structure_snapshot(symbol, bars)
    lineage = _build_lineage(symbol, db, calculation_version, provider_name)
    snapshot.lineage = lineage
    _persist_gold(
        db,
        models.MarketStructureSnapshotRecord,
        symbol,
        lineage,
        snapshot.model_dump(mode="json"),
    )
    return snapshot


def build_gold_risk(
    symbol: str,
    db: Session,
    benchmark_symbol: str | None = None,
    calculation_version: str = DEFAULT_CALCULATION_VERSION,
    provider_name: str = "yahoo",
) -> RiskSnapshot:
    bars = get_silver_bars(symbol, db)
    benchmark_bars = get_silver_bars(benchmark_symbol, db) if benchmark_symbol else None
    if not benchmark_bars:
        benchmark_bars, benchmark_symbol = None, None

    snapshot = compute_risk_snapshot(
        symbol, bars, benchmark_bars=benchmark_bars, benchmark_symbol=benchmark_symbol
    )
    lineage = _build_lineage(symbol, db, calculation_version, provider_name)
    snapshot.lineage = lineage
    _persist_gold(
        db,
        models.RiskSnapshotRecord,
        symbol,
        lineage,
        snapshot.model_dump(mode="json"),
        extra_fields={"benchmark_symbol": benchmark_symbol},
    )
    return snapshot


def build_gold_volume_liquidity(
    symbol: str,
    db: Session,
    calculation_version: str = DEFAULT_CALCULATION_VERSION,
    provider_name: str = "yahoo",
) -> VolumeLiquiditySnapshot:
    bars = get_silver_bars(symbol, db)
    snapshot = compute_volume_liquidity_snapshot(symbol, bars)
    lineage = _build_lineage(symbol, db, calculation_version, provider_name)
    snapshot.lineage = lineage
    _persist_gold(
        db,
        models.VolumeLiquiditySnapshotRecord,
        symbol,
        lineage,
        snapshot.model_dump(mode="json"),
    )
    return snapshot


def build_gold_market_context(
    symbol: str,
    db: Session,
    market_symbol: str | None = None,
    sector_symbol: str | None = None,
    calculation_version: str = DEFAULT_CALCULATION_VERSION,
    provider_name: str = "yahoo",
) -> MarketContextSnapshot:
    bars = get_silver_bars(symbol, db)
    market_bars = get_silver_bars(market_symbol, db) if market_symbol else None
    if not market_bars:
        market_bars, market_symbol = None, None
    sector_bars = get_silver_bars(sector_symbol, db) if sector_symbol else None
    if not sector_bars:
        sector_bars, sector_symbol = None, None

    snapshot = compute_market_context_snapshot(
        symbol,
        bars,
        market_bars=market_bars,
        market_symbol=market_symbol,
        sector_bars=sector_bars,
        sector_symbol=sector_symbol,
    )
    lineage = _build_lineage(symbol, db, calculation_version, provider_name)
    snapshot.lineage = lineage
    _persist_gold(
        db,
        models.MarketContextSnapshotRecord,
        symbol,
        lineage,
        snapshot.model_dump(mode="json"),
        extra_fields={"market_symbol": market_symbol, "sector_symbol": sector_symbol},
    )
    return snapshot
