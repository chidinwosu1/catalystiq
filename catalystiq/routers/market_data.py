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
)
from catalystiq.schemas.validation import DataQualityReport

# Cap batch size so one request can't fan out into hundreds of provider calls.
_MAX_BATCH_SYMBOLS = 50

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
