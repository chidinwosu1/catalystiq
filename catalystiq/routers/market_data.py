"""Market-data endpoints: raw provider pass-throughs plus the ingestion
pipeline that runs the Data Validation Layer (§2.9) before anything is
persisted as "ready for rules" (§1.1 Data Processing Layer).
"""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from catalystiq.auth import verify_action_key
from catalystiq.db.base import get_db
from catalystiq.pipelines.market_price_pipeline import build_silver, ingest_bronze, ingest_bronze_quote
from catalystiq.providers.market_data import (
    MarketDataError,
    MarketDataProvider,
    get_market_data_provider,
)
from catalystiq.schemas.market_data import (
    FundamentalsSnapshot,
    NewsItem,
    OHLCVBar,
    Quote,
    QuoteResult,
    SectorPerformance,
)
from catalystiq.schemas.validation import DataQualityReport

# Cap batch size so one request can't fan out into hundreds of provider calls.
_MAX_BATCH_SYMBOLS = 50

# SPDR sector ETFs -> GICS sector name. Deterministic sector proxy set.
_SECTOR_ETFS: list[tuple[str, str]] = [
    ("Technology", "XLK"),
    ("Financials", "XLF"),
    ("Health Care", "XLV"),
    ("Consumer Discretionary", "XLY"),
    ("Consumer Staples", "XLP"),
    ("Energy", "XLE"),
    ("Industrials", "XLI"),
    ("Materials", "XLB"),
    ("Utilities", "XLU"),
    ("Real Estate", "XLRE"),
    ("Communication Services", "XLC"),
]
# Trading sessions to look back for the "weekly" change.
_WEEK_SESSIONS = 5


def _pct_change(bars: list[OHLCVBar], lookback: int) -> float | None:
    """Percent change of the latest close vs. `lookback` sessions earlier.
    None if there isn't enough history (never fabricated)."""
    if len(bars) <= lookback:
        return None
    latest = bars[-1].close
    prior = bars[-1 - lookback].close
    if not prior:
        return None
    return (latest / prior - 1.0) * 100.0

router = APIRouter(
    prefix="/market-data",
    tags=["market-data"],
    dependencies=[Depends(verify_action_key)],
)


@router.get("/quote/{symbol}", response_model=Quote)
def get_quote(symbol: str, provider: MarketDataProvider = Depends(get_market_data_provider)):
    try:
        return provider.get_quote(symbol)
    except MarketDataError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/quotes", response_model=list[QuoteResult])
def get_quotes(
    symbols: str = Query(..., description="Comma-separated symbols, e.g. AAPL,MSFT,^VIX"),
    provider: MarketDataProvider = Depends(get_market_data_provider),
):
    """Batch quotes for a ticker/index list (ticker strip, market overview).
    Each symbol is fetched independently; a failure is reported as
    status="unavailable" for that symbol only - the batch never fails as a
    whole, and no value is fabricated."""
    seen: set[str] = set()
    results: list[QuoteResult] = []
    for raw in symbols.split(","):
        sym = raw.strip().upper()
        if not sym or sym in seen:
            continue
        seen.add(sym)
        if len(seen) > _MAX_BATCH_SYMBOLS:
            break
        try:
            q = provider.get_quote(sym)
            change = change_pct = None
            if q.price is not None and q.previous_close:
                change = q.price - q.previous_close
                change_pct = (change / q.previous_close) * 100.0
            results.append(
                QuoteResult(
                    symbol=sym, status="ok", price=q.price,
                    previous_close=q.previous_close, change=change,
                    change_pct=change_pct, as_of=q.as_of,
                )
            )
        except MarketDataError as exc:
            results.append(QuoteResult(symbol=sym, status="unavailable", detail=str(exc)))
    return results


@router.get("/ohlcv/{symbol}", response_model=list[OHLCVBar])
def get_ohlcv(
    symbol: str,
    days: int = Query(default=365, gt=0, le=3650),
    provider: MarketDataProvider = Depends(get_market_data_provider),
):
    try:
        return provider.get_ohlcv(symbol, start=dt.date.today() - dt.timedelta(days=days))
    except MarketDataError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/sectors", response_model=list[SectorPerformance])
def get_sectors(provider: MarketDataProvider = Depends(get_market_data_provider)):
    """Deterministic sector performance across the SPDR sector ETFs: 1-day and
    1-week change and relative strength vs SPY, computed from real OHLCV. A
    sector whose ETF can't be fetched is reported unavailable (no fabrication)."""
    start = dt.date.today() - dt.timedelta(days=20)

    # SPY weekly change as the relative-strength baseline (best-effort).
    spy_weekly: float | None = None
    try:
        spy_weekly = _pct_change(provider.get_ohlcv("SPY", start=start), _WEEK_SESSIONS)
    except MarketDataError:
        spy_weekly = None

    results: list[SectorPerformance] = []
    for sector, symbol in _SECTOR_ETFS:
        try:
            bars = provider.get_ohlcv(symbol, start=start)
        except MarketDataError:
            results.append(SectorPerformance(sector=sector, symbol=symbol, status="unavailable"))
            continue
        weekly = _pct_change(bars, _WEEK_SESSIONS)
        rel = weekly - spy_weekly if (weekly is not None and spy_weekly is not None) else None
        results.append(
            SectorPerformance(
                sector=sector, symbol=symbol, status="ok",
                daily_pct=_pct_change(bars, 1), weekly_pct=weekly,
                rel_strength_vs_spy=rel, as_of=bars[-1].date if bars else None,
            )
        )
    return results


@router.get("/fundamentals/{symbol}", response_model=FundamentalsSnapshot)
def get_fundamentals(
    symbol: str, provider: MarketDataProvider = Depends(get_market_data_provider)
):
    try:
        return provider.get_fundamentals(symbol)
    except MarketDataError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/news/{symbol}", response_model=list[NewsItem])
def get_news(
    symbol: str,
    limit: int = Query(default=10, gt=0, le=50),
    provider: MarketDataProvider = Depends(get_market_data_provider),
):
    try:
        return provider.get_news(symbol, limit=limit)
    except MarketDataError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/ingest/{symbol}", response_model=DataQualityReport)
def ingest_price_history(
    symbol: str,
    days: int = Query(default=365 * 5, gt=0, le=3650),
    provider: MarketDataProvider = Depends(get_market_data_provider),
    db: Session = Depends(get_db),
):
    """Runs the Bronze -> Silver pipeline (catalystiq/pipelines/
    market_price_pipeline.py): fetches raw OHLCV into an append-only Bronze
    ingestion run, then validates/cleans/upserts into Silver. Re-running
    ingestion is safe to repeat - Bronze never overwrites a prior run, and
    Silver upserts are idempotent per ticker+date. The live quote used for
    cross-validation is persisted (BronzeMarketQuote) rather than fetched
    and discarded; a quote-fetch failure doesn't block ingestion - it just
    means this build has no live-quote cross-check, same as if none had
    been requested.
    """
    try:
        run = ingest_bronze(symbol, days, provider, db)
    except MarketDataError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    live_quote = ingest_bronze_quote(symbol, provider, db, ingestion_run=run)
    result = build_silver(symbol, db, ingestion_run=run, live_quote=live_quote)
    return result.report
