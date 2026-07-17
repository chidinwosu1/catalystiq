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
of the underlying yfinance response, so Bronze's payload is source-aligned,
not byte-for-byte raw (see BronzeMarketPriceBar's docstring in db/models.py).

Full lineage chain:

    GoldCalculationRun -> SilverBuildRun -> [BronzeIngestionRun, ...]

- one SilverBuildRun may span multiple BronzeIngestionRuns (a full
  historical backfill plus subsequent incremental ingests, for example);
  one GoldCalculationRun may depend on multiple symbols' SilverBuildRuns
  (Risk's benchmark, Market Context's market/sector ETFs) - see
  GoldCalculationRunDependency.

Stage contracts:
  ingest_bronze() - fetches from the provider, writes Bronze rows plus
    full request metadata (interval, date range, adapter version). Never
    touches Silver. Each call is a new, additive ingestion run - it never
    overwrites a prior run's Bronze rows.
  ingest_bronze_quote() - fetches a live/previous-close quote and persists
    it (BronzeMarketQuote) instead of using it once and discarding it.
    Best-effort: a quote failure returns None rather than blocking
    ingestion.
  build_silver() - reads ONLY from Bronze - by default the latest Bronze
    row per bar_date across every succeeded ingestion run for the symbol
    (so a build naturally spans multiple runs), or a caller-specified
    subset. Runs the existing Data Validation Layer, upserts passing bars
    into the live, current-state SilverPriceBar table (idempotent - keyed
    on ticker+date) AND snapshots every bar it wrote into the immutable
    SilverBuildRunBar table, so a Gold calculation pinned to this build's
    id stays reproducible even after a later build changes the live
    table. Quarantines per-bar-invalidating issues into
    SilverPriceBarRejected. A malformed Bronze payload (fails to parse
    back into OHLCVBar) is skipped and marks the build "partial" rather
    than failing the whole build.
  build_gold_*() - read ONLY from the live Silver table via
    get_silver_bars() (never call the provider), call the existing pure
    compute functions in catalystiq/analysis/*.py unchanged, attach a
    Lineage block (including any benchmark/market/sector dependencies),
    persist a row keyed on the full Gold identity tuple (ticker, date,
    timeframe, calculation_version, configuration_version,
    silver_build_run_id), and return the Pydantic response. If a row
    already exists for that exact identity, it's reused instead of
    recomputed - the old row for a now-superseded silver_build_run_id is
    never overwritten, which is what keeps an older Gold snapshot
    reproducible after newer Bronze/Silver data arrives.
  ensure_fresh() - the one place a router-triggered flow is allowed to
    touch the provider: runs ingest_bronze() + ingest_bronze_quote() +
    build_silver() if Silver is stale per FreshnessPolicy (calendar-aware,
    not a flat max-age rule). Routers call this before build_gold_*(); the
    Gold functions themselves never call it.
"""
from __future__ import annotations

import datetime as dt
from enum import Enum

from sqlalchemy.orm import Session

from catalystiq.analysis.config import get_configuration_version, get_effective_config
from catalystiq.analysis.indicators import compute_technical_snapshot
from catalystiq.analysis.market_context import compute_market_context_snapshot
from catalystiq.analysis.market_structure import compute_market_structure_snapshot
from catalystiq.analysis.risk import compute_risk_snapshot
from catalystiq.analysis.volume_liquidity import compute_volume_liquidity_snapshot
from catalystiq.db import models
from catalystiq.pipelines.freshness import FreshnessPolicy
from catalystiq.providers.market_data import MarketDataError, MarketDataProvider
from catalystiq.schemas.analysis import Lineage, LineageDependency, TechnicalSnapshot
from catalystiq.schemas.market_context import MarketContextSnapshot
from catalystiq.schemas.market_data import OHLCVBar, Quote
from catalystiq.schemas.market_structure import MarketStructureSnapshot
from catalystiq.schemas.risk import RiskSnapshot
from catalystiq.schemas.validation import DataQualityIssueType, DataQualityReport
from catalystiq.schemas.volume_liquidity import VolumeLiquiditySnapshot
from catalystiq.validation.data_quality import validate_price_history

DOMAIN = "market_price"
DEFAULT_CALCULATION_VERSION = "1.0.0"
DEFAULT_TIMEFRAME = "1d"

_freshness_policy = FreshnessPolicy()


class GoldProduct(str, Enum):
    TECHNICAL = "technical"
    MARKET_STRUCTURE = "market_structure"
    RISK = "risk"
    VOLUME_LIQUIDITY = "volume_liquidity"
    MARKET_CONTEXT = "market_context"


def _get_or_create_ticker(db: Session, symbol: str) -> models.Ticker:
    symbol = symbol.upper()
    ticker = db.query(models.Ticker).filter_by(symbol=symbol).one_or_none()
    if ticker is None:
        ticker = models.Ticker(symbol=symbol)
        db.add(ticker)
        db.flush()
    return ticker


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


# --- Bronze ------------------------------------------------------------


def ingest_bronze(
    symbol: str,
    days: int,
    provider: MarketDataProvider,
    db: Session,
    interval: str = "1d",
) -> models.BronzeIngestionRun:
    """Fetches raw OHLCV from the provider and writes Bronze rows, plus
    full request metadata for reproducibility. Never reads or writes
    Silver. Safe to call repeatedly - each call is a new run, never
    overwriting a previous run's rows."""
    symbol = symbol.upper()
    ticker = _get_or_create_ticker(db, symbol)
    now = _now()
    start = dt.date.today() - dt.timedelta(days=days)
    end = dt.date.today()

    run = models.BronzeIngestionRun(
        domain=DOMAIN,
        requested_symbol=symbol,
        requested_interval=interval,
        requested_start=start,
        requested_end=end,
        request_params={
            "symbol": symbol,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "interval": interval,
            "days": days,
        },
        provider=type(provider).__name__,
        provider_adapter_version=getattr(provider, "ADAPTER_VERSION", None),
        provider_timezone=None,
        requested_at=now,
        started_at=now,
        status="running",
        bars_fetched=0,
    )
    db.add(run)
    db.flush()

    try:
        bars = provider.get_ohlcv(symbol, start=start, interval=interval)
    except MarketDataError as exc:
        run.status = "failed"
        run.completed_at = _now()
        run.error_detail = str(exc)
        db.commit()
        raise

    ingested_at = _now()
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


def ingest_bronze_quote(
    symbol: str,
    provider: MarketDataProvider,
    db: Session,
    ingestion_run: models.BronzeIngestionRun | None = None,
) -> Quote | None:
    """Fetches a live/previous-close quote and persists it
    (BronzeMarketQuote) rather than fetching-then-discarding it. Best-
    effort: swallows MarketDataError and returns None so a quote outage
    never blocks Bronze/Silver from proceeding without it - the caller
    just gets no live-quote cross-check for this build, same as if no
    quote had ever been requested."""
    symbol = symbol.upper()
    ticker = _get_or_create_ticker(db, symbol)
    try:
        quote = provider.get_quote(symbol)
    except MarketDataError:
        return None

    now = _now()
    quote_as_of = quote.as_of.replace(tzinfo=None) if quote.as_of.tzinfo else quote.as_of
    db.add(
        models.BronzeMarketQuote(
            ingestion_run_id=ingestion_run.id if ingestion_run else None,
            ticker_id=ticker.id,
            source_symbol=symbol,
            price=quote.price,
            previous_close=quote.previous_close,
            quote_as_of=quote_as_of,
            raw_payload=quote.model_dump(mode="json"),
            ingested_at=now,
        )
    )
    db.commit()
    return quote


# --- Silver --------------------------------------------------------------


class SilverBuildResult:
    def __init__(
        self,
        upserted: int,
        rejected: int,
        ticker_id: int,
        report: DataQualityReport | None = None,
        silver_build_run: models.SilverBuildRun | None = None,
    ):
        self.upserted = upserted
        self.rejected = rejected
        self.ticker_id = ticker_id
        self.report = report
        self.silver_build_run = silver_build_run


# Issue types that disqualify the specific bar they're attached to, rather
# than just flagging an otherwise-valid bar (e.g. an abnormal gap is real,
# unusual price action - not invalid data - so it's flagged, not rejected).
_REJECTING_ISSUE_TYPES = {DataQualityIssueType.INVALID_OHLC_RELATIONSHIP}


def build_silver(
    symbol: str,
    db: Session,
    ingestion_run: models.BronzeIngestionRun | None = None,
    ingestion_runs: list[models.BronzeIngestionRun] | None = None,
    live_quote: Quote | None = None,
) -> SilverBuildResult:
    """Reads ONLY from Bronze - never calls the provider itself.
    `live_quote`, if given, must already have been fetched by the caller
    from an approved real-time adapter - this function never fabricates
    one from Bronze data.

    Source rows: an explicit `ingestion_run` or `ingestion_runs` list uses
    exactly those run(s); otherwise every succeeded Bronze run for the
    symbol is used, taking the latest row per bar_date across all of them
    (so a full historical backfill plus later incremental runs combine
    naturally into one build). Every distinct contributing run is recorded
    in the SilverBuildRun<->BronzeIngestionRun association table.

    Runs the existing Data Validation Layer, upserts passing bars into the
    live SilverPriceBar table (idempotent - keyed on ticker+date) AND
    snapshots them immutably into SilverBuildRunBar, quarantines bars with
    a per-bar-invalidating issue into SilverPriceBarRejected, and skips
    (without failing the whole build) any Bronze row that doesn't parse
    back into an OHLCVBar - marking the build "partial" instead."""
    symbol = symbol.upper()
    ticker = _get_or_create_ticker(db, symbol)

    if ingestion_runs is not None:
        runs = list(ingestion_runs)
    elif ingestion_run is not None:
        runs = [ingestion_run]
    else:
        runs = (
            db.query(models.BronzeIngestionRun)
            .filter_by(domain=DOMAIN, requested_symbol=symbol, status="succeeded")
            .order_by(models.BronzeIngestionRun.id)
            .all()
        )

    if not runs:
        return SilverBuildResult(upserted=0, rejected=0, ticker_id=ticker.id)

    now = _now()
    build_run = models.SilverBuildRun(
        ticker_id=ticker.id,
        started_at=now,
        status="running",
        bars_upserted=0,
        bars_rejected=0,
        quote_used=live_quote is not None,
        created_at=now,
    )
    db.add(build_run)
    db.flush()

    for run in runs:
        db.add(
            models.SilverBuildRunBronzeIngestionRun(
                silver_build_run_id=build_run.id, bronze_ingestion_run_id=run.id
            )
        )

    bronze_rows = (
        db.query(models.BronzeMarketPriceBar)
        .filter(
            models.BronzeMarketPriceBar.ingestion_run_id.in_([r.id for r in runs]),
            models.BronzeMarketPriceBar.ticker_id == ticker.id,
        )
        .order_by(
            models.BronzeMarketPriceBar.bar_date,
            models.BronzeMarketPriceBar.ingested_at,
            models.BronzeMarketPriceBar.id,
        )
        .all()
    )
    # Latest row per date across every selected run (later in sort order
    # overwrites earlier for the same date).
    latest_bronze_by_date: dict[dt.date, models.BronzeMarketPriceBar] = {}
    for row in bronze_rows:
        latest_bronze_by_date[row.bar_date] = row

    raw_bars: list[OHLCVBar] = []
    bronze_by_date: dict[dt.date, models.BronzeMarketPriceBar] = {}
    malformed_count = 0
    for bar_date, row in latest_bronze_by_date.items():
        try:
            bar = OHLCVBar(**row.raw_payload)
        except Exception:
            malformed_count += 1
            continue
        raw_bars.append(bar)
        bronze_by_date[bar_date] = row

    try:
        cleaned_bars, report = validate_price_history(symbol, raw_bars, live_quote=live_quote)

        issues_by_date: dict[dt.date, list] = {}
        for issue in report.issues:
            if issue.date is not None:
                issues_by_date.setdefault(issue.date, []).append(issue)

        upserted = 0
        rejected = 0

        for bar in cleaned_bars:
            bar_issues = issues_by_date.get(bar.date, [])
            rejecting = [i for i in bar_issues if i.type in _REJECTING_ISSUE_TYPES]
            bronze_row = bronze_by_date.get(bar.date)

            if rejecting:
                db.add(
                    models.SilverPriceBarRejected(
                        ticker_id=ticker.id,
                        source_bronze_market_price_bar_id=(
                            bronze_row.id if bronze_row else bronze_rows[0].id
                        ),
                        silver_build_run_id=build_run.id,
                        bar_date=bar.date,
                        rejection_reason="; ".join(i.detail for i in rejecting),
                        rejected_at=now,
                    )
                )
                rejected += 1
                continue

            status = "clean_with_warnings" if bar_issues else "clean"
            remediation = [i.model_dump(mode="json") for i in bar_issues] or None
            source_run_id = bronze_row.ingestion_run_id if bronze_row else None

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
                        source_bronze_ingestion_run_id=source_run_id,
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
                existing.source_bronze_ingestion_run_id = source_run_id
                existing.data_quality_status = status
                existing.remediation_actions = remediation
                existing.updated_at = now

            db.add(
                models.SilverBuildRunBar(
                    silver_build_run_id=build_run.id,
                    ticker_id=ticker.id,
                    bar_date=bar.date,
                    open=bar.open,
                    high=bar.high,
                    low=bar.low,
                    close=bar.close,
                    volume=bar.volume,
                    data_quality_status=status,
                    remediation_actions=remediation,
                )
            )
            upserted += 1
    except Exception:
        build_run.status = "failed"
        build_run.completed_at = _now()
        db.commit()
        raise

    if malformed_count > 0 and upserted == 0 and rejected == 0:
        build_run.status = "failed"
    elif malformed_count > 0:
        build_run.status = "partial"
    else:
        build_run.status = "succeeded"

    build_run.bars_upserted = upserted
    build_run.bars_rejected = rejected
    build_run.completed_at = _now()
    build_run.validation_report = report.model_dump(mode="json")
    db.commit()
    return SilverBuildResult(
        upserted=upserted,
        rejected=rejected,
        ticker_id=ticker.id,
        report=report,
        silver_build_run=build_run,
    )


def ensure_fresh(
    symbol: str,
    provider: MarketDataProvider,
    db: Session,
    days: int = 365 * 5,
    interval: str = "1d",
) -> models.SilverBuildRun | None:
    """Runs ingest_bronze() + ingest_bronze_quote() + build_silver() if
    Silver is stale per FreshnessPolicy (calendar-aware: for daily bars,
    "fresh" means Silver already has the most recent completed session -
    weekends/holidays never trigger a re-ingest). No-ops (returns None)
    otherwise. The only place in this module that touches the provider on
    a router-triggered, on-demand basis - build_gold_*() below never do."""
    symbol = symbol.upper()
    ticker = db.query(models.Ticker).filter_by(symbol=symbol).one_or_none()

    latest_date: dt.date | None = None
    if ticker is not None:
        latest_row = (
            db.query(models.SilverPriceBar)
            .filter_by(ticker_id=ticker.id)
            .order_by(models.SilverPriceBar.date.desc())
            .first()
        )
        if latest_row is not None:
            latest_date = latest_row.date

    now = dt.datetime.now(dt.timezone.utc)
    if not _freshness_policy.is_stale(latest_date, now, interval=interval):
        return None

    run = ingest_bronze(symbol, days, provider, db, interval=interval)
    live_quote = ingest_bronze_quote(symbol, provider, db, ingestion_run=run)
    result = build_silver(symbol, db, ingestion_run=run, live_quote=live_quote)
    return result.silver_build_run


# --- Gold read boundary ----------------------------------------------------


def get_silver_bars(symbol: str, db: Session) -> list[OHLCVBar]:
    """The ONLY data-read path a Gold compute function may use. Reads the
    live, current-state SilverPriceBar rows for `symbol` - correct for a
    fresh calculation immediately following ensure_fresh() in the same
    request, since nothing else mutates Silver in between. Reproducing an
    *older* Gold result doesn't recompute from here - it comes from that
    older Gold row's own frozen `payload`, keyed to the exact
    silver_build_run_id it was computed from (see build_gold_*() below)."""
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


def get_latest_silver_build_run(symbol: str, db: Session) -> models.SilverBuildRun | None:
    """The current (most recent usable) SilverBuildRun for `symbol` -
    "usable" meaning succeeded or partial (a partial build's non-malformed
    rows are still valid, reproducible Silver data)."""
    symbol = symbol.upper()
    ticker = db.query(models.Ticker).filter_by(symbol=symbol).one_or_none()
    if ticker is None:
        return None
    return (
        db.query(models.SilverBuildRun)
        .filter(
            models.SilverBuildRun.ticker_id == ticker.id,
            models.SilverBuildRun.status.in_(["succeeded", "partial"]),
        )
        .order_by(models.SilverBuildRun.id.desc())
        .first()
    )


def _resolve_dependency_build(
    symbol: str | None, db: Session
) -> tuple[models.SilverBuildRun | None, int]:
    if not symbol:
        return None, 0
    run = get_latest_silver_build_run(symbol, db)
    if run is None:
        return None, 0
    count = db.query(models.SilverBuildRunBar).filter_by(silver_build_run_id=run.id).count()
    return run, count


def _as_of_date(silver_build_run: models.SilverBuildRun | None, db: Session) -> dt.date:
    if silver_build_run is not None:
        latest = (
            db.query(models.SilverBuildRunBar.bar_date)
            .filter_by(silver_build_run_id=silver_build_run.id)
            .order_by(models.SilverBuildRunBar.bar_date.desc())
            .first()
        )
        if latest:
            return latest[0]
    return dt.date.today()


def _lineage_from_build(
    silver_build_run: models.SilverBuildRun | None,
    record_count: int,
    calculation_version: str,
    configuration_version: str,
    provider_name: str,
    db: Session,
    dependencies: list[LineageDependency],
) -> Lineage:
    now = dt.datetime.now(dt.timezone.utc)
    if silver_build_run is None:
        return Lineage(
            calculation_version=calculation_version,
            configuration_version=configuration_version,
            silver_record_count=0,
            source_provider=provider_name,
            calculated_at=now,
            dependencies=dependencies,
        )

    dates = [
        r[0]
        for r in db.query(models.SilverBuildRunBar.bar_date)
        .filter_by(silver_build_run_id=silver_build_run.id)
        .all()
    ]
    run_ids = [
        r[0]
        for r in db.query(models.SilverBuildRunBronzeIngestionRun.bronze_ingestion_run_id)
        .filter_by(silver_build_run_id=silver_build_run.id)
        .all()
    ]
    return Lineage(
        calculation_version=calculation_version,
        configuration_version=configuration_version,
        silver_record_count=record_count,
        silver_date_range_start=min(dates) if dates else None,
        silver_date_range_end=max(dates) if dates else None,
        bronze_ingestion_run_id=max(run_ids) if run_ids else None,
        silver_build_run_id=silver_build_run.id,
        source_provider=provider_name,
        calculated_at=now,
        dependencies=dependencies,
    )


def _lineage_from_gold_row(db: Session, row, provider_name: str) -> Lineage:
    dependencies: list[LineageDependency] = []
    if row.gold_calculation_run_id is not None:
        dep_rows = (
            db.query(models.GoldCalculationRunDependency)
            .filter_by(gold_calculation_run_id=row.gold_calculation_run_id)
            .all()
        )
        dependencies = [
            LineageDependency(
                role=d.role,
                symbol=d.symbol,
                silver_record_count=d.silver_record_count,
                silver_build_run_id=d.silver_build_run_id,
            )
            for d in dep_rows
        ]
    calculated_at = row.created_at
    if calculated_at.tzinfo is None:
        calculated_at = calculated_at.replace(tzinfo=dt.timezone.utc)
    return Lineage(
        calculation_version=row.calculation_version,
        configuration_version=row.configuration_version,
        silver_record_count=row.silver_record_count,
        silver_date_range_start=row.silver_date_range_start,
        silver_date_range_end=row.silver_date_range_end,
        bronze_ingestion_run_id=row.bronze_ingestion_run_id,
        silver_build_run_id=row.silver_build_run_id,
        source_provider=row.source_provider,
        calculated_at=calculated_at,
        dependencies=dependencies,
    )


def _start_gold_run(
    db: Session,
    ticker: models.Ticker,
    product_name: str,
    timeframe: str,
    calculation_version: str,
    configuration_version: str,
    silver_build_run: models.SilverBuildRun | None,
) -> models.GoldCalculationRun:
    now = _now()
    run = models.GoldCalculationRun(
        ticker_id=ticker.id,
        product_name=product_name,
        timeframe=timeframe,
        calculation_version=calculation_version,
        configuration_version=configuration_version,
        configuration_snapshot=get_effective_config(product_name),
        silver_build_run_id=silver_build_run.id if silver_build_run else None,
        status="running",
        started_at=now,
        created_at=now,
    )
    db.add(run)
    db.flush()
    return run


def _finish_gold_run(db: Session, gold_run: models.GoldCalculationRun, status: str) -> None:
    gold_run.status = status
    gold_run.completed_at = _now()


def _record_dependency(db: Session, gold_run: models.GoldCalculationRun, dep: LineageDependency) -> None:
    db.add(
        models.GoldCalculationRunDependency(
            gold_calculation_run_id=gold_run.id,
            role=dep.role,
            symbol=dep.symbol,
            silver_build_run_id=dep.silver_build_run_id,
            silver_record_count=dep.silver_record_count,
        )
    )


def _cached_gold_row(
    db: Session,
    record_cls,
    ticker_id: int,
    as_of_date: dt.date,
    timeframe: str,
    calculation_version: str,
    configuration_version: str,
    silver_build_run_id: int | None,
):
    if silver_build_run_id is None:
        return None
    return (
        db.query(record_cls)
        .filter_by(
            ticker_id=ticker_id,
            date=as_of_date,
            timeframe=timeframe,
            calculation_version=calculation_version,
            configuration_version=configuration_version,
            silver_build_run_id=silver_build_run_id,
            data_quality_status="available",
        )
        .one_or_none()
    )


def _persist_gold(
    db: Session,
    record_cls,
    ticker: models.Ticker,
    as_of_date: dt.date,
    timeframe: str,
    calculation_version: str,
    configuration_version: str,
    gold_run: models.GoldCalculationRun,
    lineage: Lineage,
    payload: dict,
    source_provider: str,
    extra_fields: dict | None = None,
) -> None:
    now = _now()
    fields = {
        "payload": payload,
        "data_quality_status": "available",
        "silver_record_count": lineage.silver_record_count,
        "silver_date_range_start": lineage.silver_date_range_start,
        "silver_date_range_end": lineage.silver_date_range_end,
        "bronze_ingestion_run_id": lineage.bronze_ingestion_run_id,
        "source_provider": source_provider,
        "timeframe": timeframe,
        "configuration_version": configuration_version,
        "gold_calculation_run_id": gold_run.id,
        "silver_build_run_id": lineage.silver_build_run_id,
        **(extra_fields or {}),
    }

    existing = (
        db.query(record_cls)
        .filter_by(
            ticker_id=ticker.id,
            date=as_of_date,
            timeframe=timeframe,
            calculation_version=calculation_version,
            configuration_version=configuration_version,
            silver_build_run_id=lineage.silver_build_run_id,
        )
        .one_or_none()
    )
    if existing is None:
        db.add(
            record_cls(
                ticker_id=ticker.id,
                date=as_of_date,
                calculation_version=calculation_version,
                created_at=now,
                **fields,
            )
        )
    else:
        for key, value in fields.items():
            setattr(existing, key, value)


# --- Gold products -----------------------------------------------------


def build_gold_technical(
    symbol: str,
    db: Session,
    calculation_version: str = DEFAULT_CALCULATION_VERSION,
    provider_name: str = "yahoo",
    timeframe: str = DEFAULT_TIMEFRAME,
) -> TechnicalSnapshot:
    symbol = symbol.upper()
    ticker = _get_or_create_ticker(db, symbol)
    silver_build_run, record_count = _resolve_dependency_build(symbol, db)
    configuration_version = get_configuration_version("technical")
    as_of_date = _as_of_date(silver_build_run, db)

    cached = _cached_gold_row(
        db,
        models.TechnicalSnapshotRecord,
        ticker.id,
        as_of_date,
        timeframe,
        calculation_version,
        configuration_version,
        silver_build_run.id if silver_build_run else None,
    )
    if cached is not None:
        snapshot = TechnicalSnapshot.model_validate(cached.payload)
        snapshot.lineage = _lineage_from_gold_row(db, cached, provider_name)
        return snapshot

    gold_run = _start_gold_run(
        db, ticker, "technical", timeframe, calculation_version, configuration_version, silver_build_run
    )
    try:
        bars = get_silver_bars(symbol, db)
        snapshot = compute_technical_snapshot(symbol, bars)

        dep = LineageDependency(
            role="primary",
            symbol=symbol,
            silver_record_count=record_count,
            silver_build_run_id=silver_build_run.id if silver_build_run else None,
        )
        _record_dependency(db, gold_run, dep)
        lineage = _lineage_from_build(
            silver_build_run, record_count, calculation_version, configuration_version, provider_name, db, [dep]
        )
        snapshot.lineage = lineage
        _persist_gold(
            db,
            models.TechnicalSnapshotRecord,
            ticker,
            as_of_date,
            timeframe,
            calculation_version,
            configuration_version,
            gold_run,
            lineage,
            snapshot.model_dump(mode="json"),
            provider_name,
        )
        _finish_gold_run(db, gold_run, "succeeded")
        db.commit()
        return snapshot
    except Exception:
        _finish_gold_run(db, gold_run, "failed")
        db.commit()
        raise


def build_gold_market_structure(
    symbol: str,
    db: Session,
    calculation_version: str = DEFAULT_CALCULATION_VERSION,
    provider_name: str = "yahoo",
    timeframe: str = DEFAULT_TIMEFRAME,
) -> MarketStructureSnapshot:
    symbol = symbol.upper()
    ticker = _get_or_create_ticker(db, symbol)
    silver_build_run, record_count = _resolve_dependency_build(symbol, db)
    configuration_version = get_configuration_version("market_structure")
    as_of_date = _as_of_date(silver_build_run, db)

    cached = _cached_gold_row(
        db,
        models.MarketStructureSnapshotRecord,
        ticker.id,
        as_of_date,
        timeframe,
        calculation_version,
        configuration_version,
        silver_build_run.id if silver_build_run else None,
    )
    if cached is not None:
        snapshot = MarketStructureSnapshot.model_validate(cached.payload)
        snapshot.lineage = _lineage_from_gold_row(db, cached, provider_name)
        return snapshot

    gold_run = _start_gold_run(
        db, ticker, "market_structure", timeframe, calculation_version, configuration_version, silver_build_run
    )
    try:
        bars = get_silver_bars(symbol, db)
        snapshot = compute_market_structure_snapshot(symbol, bars)

        dep = LineageDependency(
            role="primary",
            symbol=symbol,
            silver_record_count=record_count,
            silver_build_run_id=silver_build_run.id if silver_build_run else None,
        )
        _record_dependency(db, gold_run, dep)
        lineage = _lineage_from_build(
            silver_build_run, record_count, calculation_version, configuration_version, provider_name, db, [dep]
        )
        snapshot.lineage = lineage
        _persist_gold(
            db,
            models.MarketStructureSnapshotRecord,
            ticker,
            as_of_date,
            timeframe,
            calculation_version,
            configuration_version,
            gold_run,
            lineage,
            snapshot.model_dump(mode="json"),
            provider_name,
        )
        _finish_gold_run(db, gold_run, "succeeded")
        db.commit()
        return snapshot
    except Exception:
        _finish_gold_run(db, gold_run, "failed")
        db.commit()
        raise


def build_gold_risk(
    symbol: str,
    db: Session,
    benchmark_symbol: str | None = None,
    calculation_version: str = DEFAULT_CALCULATION_VERSION,
    provider_name: str = "yahoo",
    timeframe: str = DEFAULT_TIMEFRAME,
) -> RiskSnapshot:
    symbol = symbol.upper()
    ticker = _get_or_create_ticker(db, symbol)
    silver_build_run, record_count = _resolve_dependency_build(symbol, db)
    bench_build_run, bench_count = _resolve_dependency_build(benchmark_symbol, db)
    resolved_benchmark = benchmark_symbol.upper() if benchmark_symbol and bench_build_run else None

    configuration_version = get_configuration_version("risk")
    as_of_date = _as_of_date(silver_build_run, db)

    cached = _cached_gold_row(
        db,
        models.RiskSnapshotRecord,
        ticker.id,
        as_of_date,
        timeframe,
        calculation_version,
        configuration_version,
        silver_build_run.id if silver_build_run else None,
    )
    if cached is not None:
        snapshot = RiskSnapshot.model_validate(cached.payload)
        snapshot.lineage = _lineage_from_gold_row(db, cached, provider_name)
        return snapshot

    gold_run = _start_gold_run(
        db, ticker, "risk", timeframe, calculation_version, configuration_version, silver_build_run
    )
    try:
        bars = get_silver_bars(symbol, db)
        benchmark_bars = get_silver_bars(resolved_benchmark, db) if resolved_benchmark else None
        if not benchmark_bars:
            benchmark_bars, resolved_benchmark = None, None

        snapshot = compute_risk_snapshot(
            symbol, bars, benchmark_bars=benchmark_bars, benchmark_symbol=resolved_benchmark
        )

        deps = [
            LineageDependency(
                role="primary",
                symbol=symbol,
                silver_record_count=record_count,
                silver_build_run_id=silver_build_run.id if silver_build_run else None,
            )
        ]
        if resolved_benchmark:
            deps.append(
                LineageDependency(
                    role="benchmark",
                    symbol=resolved_benchmark,
                    silver_record_count=bench_count,
                    silver_build_run_id=bench_build_run.id if bench_build_run else None,
                )
            )
        for dep in deps:
            _record_dependency(db, gold_run, dep)

        lineage = _lineage_from_build(
            silver_build_run, record_count, calculation_version, configuration_version, provider_name, db, deps
        )
        snapshot.lineage = lineage
        _persist_gold(
            db,
            models.RiskSnapshotRecord,
            ticker,
            as_of_date,
            timeframe,
            calculation_version,
            configuration_version,
            gold_run,
            lineage,
            snapshot.model_dump(mode="json"),
            provider_name,
            extra_fields={"benchmark_symbol": resolved_benchmark},
        )
        _finish_gold_run(db, gold_run, "succeeded")
        db.commit()
        return snapshot
    except Exception:
        _finish_gold_run(db, gold_run, "failed")
        db.commit()
        raise


def build_gold_volume_liquidity(
    symbol: str,
    db: Session,
    calculation_version: str = DEFAULT_CALCULATION_VERSION,
    provider_name: str = "yahoo",
    timeframe: str = DEFAULT_TIMEFRAME,
) -> VolumeLiquiditySnapshot:
    symbol = symbol.upper()
    ticker = _get_or_create_ticker(db, symbol)
    silver_build_run, record_count = _resolve_dependency_build(symbol, db)
    configuration_version = get_configuration_version("volume_liquidity")
    as_of_date = _as_of_date(silver_build_run, db)

    cached = _cached_gold_row(
        db,
        models.VolumeLiquiditySnapshotRecord,
        ticker.id,
        as_of_date,
        timeframe,
        calculation_version,
        configuration_version,
        silver_build_run.id if silver_build_run else None,
    )
    if cached is not None:
        snapshot = VolumeLiquiditySnapshot.model_validate(cached.payload)
        snapshot.lineage = _lineage_from_gold_row(db, cached, provider_name)
        return snapshot

    gold_run = _start_gold_run(
        db, ticker, "volume_liquidity", timeframe, calculation_version, configuration_version, silver_build_run
    )
    try:
        bars = get_silver_bars(symbol, db)
        snapshot = compute_volume_liquidity_snapshot(symbol, bars)

        dep = LineageDependency(
            role="primary",
            symbol=symbol,
            silver_record_count=record_count,
            silver_build_run_id=silver_build_run.id if silver_build_run else None,
        )
        _record_dependency(db, gold_run, dep)
        lineage = _lineage_from_build(
            silver_build_run, record_count, calculation_version, configuration_version, provider_name, db, [dep]
        )
        snapshot.lineage = lineage
        _persist_gold(
            db,
            models.VolumeLiquiditySnapshotRecord,
            ticker,
            as_of_date,
            timeframe,
            calculation_version,
            configuration_version,
            gold_run,
            lineage,
            snapshot.model_dump(mode="json"),
            provider_name,
        )
        _finish_gold_run(db, gold_run, "succeeded")
        db.commit()
        return snapshot
    except Exception:
        _finish_gold_run(db, gold_run, "failed")
        db.commit()
        raise


def build_gold_market_context(
    symbol: str,
    db: Session,
    market_symbol: str | None = None,
    sector_symbol: str | None = None,
    calculation_version: str = DEFAULT_CALCULATION_VERSION,
    provider_name: str = "yahoo",
    timeframe: str = DEFAULT_TIMEFRAME,
) -> MarketContextSnapshot:
    symbol = symbol.upper()
    ticker = _get_or_create_ticker(db, symbol)
    silver_build_run, record_count = _resolve_dependency_build(symbol, db)
    market_build_run, market_count = _resolve_dependency_build(market_symbol, db)
    sector_build_run, sector_count = _resolve_dependency_build(sector_symbol, db)
    resolved_market = market_symbol.upper() if market_symbol and market_build_run else None
    resolved_sector = sector_symbol.upper() if sector_symbol and sector_build_run else None

    configuration_version = get_configuration_version("market_context")
    as_of_date = _as_of_date(silver_build_run, db)

    cached = _cached_gold_row(
        db,
        models.MarketContextSnapshotRecord,
        ticker.id,
        as_of_date,
        timeframe,
        calculation_version,
        configuration_version,
        silver_build_run.id if silver_build_run else None,
    )
    if cached is not None:
        snapshot = MarketContextSnapshot.model_validate(cached.payload)
        snapshot.lineage = _lineage_from_gold_row(db, cached, provider_name)
        return snapshot

    gold_run = _start_gold_run(
        db, ticker, "market_context", timeframe, calculation_version, configuration_version, silver_build_run
    )
    try:
        bars = get_silver_bars(symbol, db)
        market_bars = get_silver_bars(resolved_market, db) if resolved_market else None
        if not market_bars:
            market_bars, resolved_market = None, None
        sector_bars = get_silver_bars(resolved_sector, db) if resolved_sector else None
        if not sector_bars:
            sector_bars, resolved_sector = None, None

        snapshot = compute_market_context_snapshot(
            symbol,
            bars,
            market_bars=market_bars,
            market_symbol=resolved_market,
            sector_bars=sector_bars,
            sector_symbol=resolved_sector,
        )

        deps = [
            LineageDependency(
                role="primary",
                symbol=symbol,
                silver_record_count=record_count,
                silver_build_run_id=silver_build_run.id if silver_build_run else None,
            )
        ]
        if resolved_market:
            deps.append(
                LineageDependency(
                    role="market",
                    symbol=resolved_market,
                    silver_record_count=market_count,
                    silver_build_run_id=market_build_run.id if market_build_run else None,
                )
            )
        if resolved_sector:
            deps.append(
                LineageDependency(
                    role="sector",
                    symbol=resolved_sector,
                    silver_record_count=sector_count,
                    silver_build_run_id=sector_build_run.id if sector_build_run else None,
                )
            )
        for dep in deps:
            _record_dependency(db, gold_run, dep)

        lineage = _lineage_from_build(
            silver_build_run, record_count, calculation_version, configuration_version, provider_name, db, deps
        )
        snapshot.lineage = lineage
        _persist_gold(
            db,
            models.MarketContextSnapshotRecord,
            ticker,
            as_of_date,
            timeframe,
            calculation_version,
            configuration_version,
            gold_run,
            lineage,
            snapshot.model_dump(mode="json"),
            provider_name,
            extra_fields={"market_symbol": resolved_market, "sector_symbol": resolved_sector},
        )
        _finish_gold_run(db, gold_run, "succeeded")
        db.commit()
        return snapshot
    except Exception:
        _finish_gold_run(db, gold_run, "failed")
        db.commit()
        raise


_GOLD_BUILDERS = {
    GoldProduct.TECHNICAL: build_gold_technical,
    GoldProduct.MARKET_STRUCTURE: build_gold_market_structure,
    GoldProduct.RISK: build_gold_risk,
    GoldProduct.VOLUME_LIQUIDITY: build_gold_volume_liquidity,
    GoldProduct.MARKET_CONTEXT: build_gold_market_context,
}


def build_gold(
    symbol: str,
    db: Session,
    requested_products: set[GoldProduct],
    **kwargs,
) -> dict[GoldProduct, object]:
    """Computes only the requested products - a technical-only request
    never touches market structure/risk/volume-liquidity/market context.
    `kwargs` (benchmark_symbol, market_symbol, sector_symbol,
    calculation_version, provider_name, timeframe) are forwarded to
    whichever build_gold_*() functions accept them; unsupported kwargs for
    a given product are dropped rather than raising, so a single shared
    call site can request multiple products with mixed-relevance kwargs."""
    import inspect

    results: dict[GoldProduct, object] = {}
    for product in requested_products:
        builder = _GOLD_BUILDERS[product]
        accepted = set(inspect.signature(builder).parameters)
        call_kwargs = {k: v for k, v in kwargs.items() if k in accepted}
        results[product] = builder(symbol, db, **call_kwargs)
    return results
