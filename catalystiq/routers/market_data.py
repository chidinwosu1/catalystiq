"""Market-data endpoints: raw provider pass-throughs plus the ingestion
pipeline that runs the Data Validation Layer (§2.9) before anything is
persisted as "ready for rules" (§1.1 Data Processing Layer).
"""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from catalystiq.auth import verify_action_key
from catalystiq.db import models
from catalystiq.db.base import get_db
from catalystiq.providers.market_data import (
    MarketDataError,
    MarketDataProvider,
    get_market_data_provider,
)
from catalystiq.schemas.market_data import FundamentalsSnapshot, NewsItem, OHLCVBar, Quote
from catalystiq.schemas.validation import DataQualityReport
from catalystiq.validation.data_quality import validate_price_history

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
    """Pulls OHLCV, runs the Data Validation Layer, and upserts cleaned bars
    into `price_history`. Existing dates are left untouched (append-only);
    re-running ingestion is safe to repeat.
    """
    try:
        raw_bars = provider.get_ohlcv(symbol, start=dt.date.today() - dt.timedelta(days=days))
        live_quote = provider.get_quote(symbol)
    except MarketDataError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    cleaned_bars, report = validate_price_history(symbol, raw_bars, live_quote=live_quote)

    ticker = db.query(models.Ticker).filter_by(symbol=symbol.upper()).one_or_none()
    if ticker is None:
        ticker = models.Ticker(symbol=symbol.upper())
        db.add(ticker)
        db.flush()

    existing_dates = {
        row.date for row in db.query(models.PriceHistory.date).filter_by(ticker_id=ticker.id)
    }
    for bar in cleaned_bars:
        if bar.date in existing_dates:
            continue
        db.add(
            models.PriceHistory(
                ticker_id=ticker.id,
                date=bar.date,
                open=bar.open,
                high=bar.high,
                low=bar.low,
                close=bar.close,
                volume=bar.volume,
            )
        )
    db.commit()

    return report
