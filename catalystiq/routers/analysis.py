"""Real technical-indicator endpoints. See catalystiq/analysis/indicators.py
for what's actually computed here versus the parts of the quantitative-
scoring spec (rating, calibrated probabilities, confidence score) that
remain out of scope until a real trained/validated model exists.
"""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Query

from catalystiq.analysis.indicators import compute_technical_snapshot
from catalystiq.auth import verify_action_key
from catalystiq.providers.market_data import (
    MarketDataError,
    MarketDataProvider,
    get_market_data_provider,
)
from catalystiq.schemas.analysis import TechnicalSnapshot

router = APIRouter(
    prefix="/analysis",
    tags=["analysis"],
    dependencies=[Depends(verify_action_key)],
)


@router.get("/technical/{symbol}", response_model=TechnicalSnapshot)
def get_technical_snapshot(
    symbol: str,
    days: int = Query(default=365 * 5, gt=0, le=3650),
    provider: MarketDataProvider = Depends(get_market_data_provider),
):
    try:
        bars = provider.get_ohlcv(symbol, start=dt.date.today() - dt.timedelta(days=days))
    except MarketDataError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return compute_technical_snapshot(symbol, bars)
