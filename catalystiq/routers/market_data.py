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
from catalystiq.pipelines.market_price_pipeline import build_silver, ingest_bronze
from catalystiq.providers.market_data import (
    MarketDataError,
    MarketDataProvider,
    get_market_data_provider,
)
from catalystiq.schemas.market_data import FundamentalsSnapshot, NewsItem, OHLCVBar, Quote
from catalystiq.schemas.validation import DataQualityReport

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
    Silver upserts are idempotent per ticker+date.
    """
    try:
        run = ingest_bronze(symbol, days, provider, db)
        live_quote = provider.get_quote(symbol)
    except MarketDataError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    result = build_silver(symbol, db, ingestion_run=run, live_quote=live_quote)
    return result.report
