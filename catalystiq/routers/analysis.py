"""Real technical-indicator endpoints. See catalystiq/analysis/indicators.py
for what's actually computed here versus the parts of the quantitative-
scoring spec (rating, calibrated probabilities, confidence score) that
remain out of scope until a real trained/validated model exists.
"""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Query

from catalystiq.analysis.indicators import compute_technical_snapshot
from catalystiq.analysis.market_structure import compute_market_structure_snapshot
from catalystiq.analysis.risk import compute_risk_snapshot
from catalystiq.analysis.market_context import SECTOR_ETF_MAP, compute_market_context_snapshot
from catalystiq.analysis.volume_liquidity import compute_volume_liquidity_snapshot
from catalystiq.auth import verify_action_key
from catalystiq.providers.market_data import (
    MarketDataError,
    MarketDataProvider,
    get_market_data_provider,
)
from catalystiq.schemas.analysis import TechnicalSnapshot
from catalystiq.schemas.market_context import MarketContextSnapshot
from catalystiq.schemas.market_structure import MarketStructureSnapshot
from catalystiq.schemas.risk import RiskSnapshot
from catalystiq.schemas.volume_liquidity import VolumeLiquiditySnapshot

router = APIRouter(
    prefix="/analysis",
    tags=["analysis"],
    dependencies=[Depends(verify_action_key)],
)


def _fetch_bars(
    provider: MarketDataProvider, symbol: str, days: int
) -> list:
    try:
        return provider.get_ohlcv(symbol, start=dt.date.today() - dt.timedelta(days=days))
    except MarketDataError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/technical/{symbol}", response_model=TechnicalSnapshot)
def get_technical_snapshot(
    symbol: str,
    days: int = Query(default=365 * 5, gt=0, le=3650),
    provider: MarketDataProvider = Depends(get_market_data_provider),
):
    bars = _fetch_bars(provider, symbol, days)
    return compute_technical_snapshot(symbol, bars)


@router.get("/{symbol}/market-structure", response_model=MarketStructureSnapshot)
def get_market_structure_snapshot(
    symbol: str,
    days: int = Query(default=365 * 5, gt=0, le=3650),
    provider: MarketDataProvider = Depends(get_market_data_provider),
):
    bars = _fetch_bars(provider, symbol, days)
    return compute_market_structure_snapshot(symbol, bars)


@router.get("/{symbol}/risk", response_model=RiskSnapshot)
def get_risk_snapshot(
    symbol: str,
    days: int = Query(default=365 * 5, gt=0, le=3650),
    benchmark: str | None = Query(default="SPY"),
    provider: MarketDataProvider = Depends(get_market_data_provider),
):
    bars = _fetch_bars(provider, symbol, days)

    benchmark_bars = None
    warning: str | None = None
    if benchmark:
        try:
            benchmark_bars = provider.get_ohlcv(
                benchmark, start=dt.date.today() - dt.timedelta(days=days)
            )
        except MarketDataError as exc:
            # Benchmark unavailability shouldn't block the rest of the risk
            # snapshot (§24 partial-degradation principle) - beta/correlation
            # just come back "not_supported" instead.
            warning = f"Benchmark {benchmark!r} unavailable: {exc}"
            benchmark = None

    snapshot = compute_risk_snapshot(symbol, bars, benchmark_bars=benchmark_bars, benchmark_symbol=benchmark)
    if warning:
        snapshot.warnings.append(warning)
    return snapshot


@router.get("/{symbol}/volume-liquidity", response_model=VolumeLiquiditySnapshot)
def get_volume_liquidity_snapshot(
    symbol: str,
    days: int = Query(default=365 * 5, gt=0, le=3650),
    provider: MarketDataProvider = Depends(get_market_data_provider),
):
    bars = _fetch_bars(provider, symbol, days)
    return compute_volume_liquidity_snapshot(symbol, bars)


@router.get("/{symbol}/market-context", response_model=MarketContextSnapshot)
def get_market_context_snapshot(
    symbol: str,
    days: int = Query(default=365 * 5, gt=0, le=3650),
    market: str | None = Query(default="SPY"),
    sector: str | None = Query(default=None, description="Sector name, e.g. 'Technology' - resolved to a sector ETF via SECTOR_ETF_MAP."),
    provider: MarketDataProvider = Depends(get_market_data_provider),
):
    bars = _fetch_bars(provider, symbol, days)
    warnings: list[str] = []

    market_bars = None
    if market:
        try:
            market_bars = provider.get_ohlcv(market, start=dt.date.today() - dt.timedelta(days=days))
        except MarketDataError as exc:
            warnings.append(f"Market benchmark {market!r} unavailable: {exc}")
            market = None

    sector_symbol = SECTOR_ETF_MAP.get(sector) if sector else None
    sector_bars = None
    if sector and not sector_symbol:
        warnings.append(f"Sector {sector!r} isn't mapped to a sector ETF; sector-relative metrics omitted.")
    elif sector_symbol:
        try:
            sector_bars = provider.get_ohlcv(sector_symbol, start=dt.date.today() - dt.timedelta(days=days))
        except MarketDataError as exc:
            warnings.append(f"Sector benchmark {sector_symbol!r} unavailable: {exc}")
            sector_symbol = None

    snapshot = compute_market_context_snapshot(
        symbol,
        bars,
        market_bars=market_bars,
        market_symbol=market,
        sector_bars=sector_bars,
        sector_symbol=sector_symbol,
    )
    snapshot.warnings.extend(warnings)
    return snapshot
