"""Real technical-indicator endpoints. See catalystiq/analysis/indicators.py
for what's actually computed here versus the parts of the quantitative-
scoring spec (rating, calibrated probabilities, confidence score) that
remain out of scope until a real trained/validated model exists.

Every endpoint here is a Gold-layer read: it calls `ensure_fresh()` to
bring Silver up to date on demand (Bronze -> Silver, only touching the
provider if Silver is missing or stale), then calls the relevant
`build_gold_*()`, which computes from Silver only and persists lineage.
See catalystiq/pipelines/market_price_pipeline.py.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from catalystiq.analysis.market_context import SECTOR_ETF_MAP
from catalystiq.auth import verify_action_key
from catalystiq.db.base import get_db
from catalystiq.pipelines.market_price_pipeline import GoldProduct, build_gold, ensure_fresh
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


def _ensure_fresh(provider: MarketDataProvider, symbol: str, db: Session, days: int) -> None:
    try:
        ensure_fresh(symbol, provider, db, days=days)
    except MarketDataError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


def _ensure_fresh_optional(
    provider: MarketDataProvider, symbol: str | None, db: Session, days: int
) -> str | None:
    """Best-effort freshness for an optional benchmark/market/sector symbol -
    unavailability shouldn't block the primary snapshot (§24 partial-
    degradation principle), so failures just drop the symbol instead of
    raising."""
    if not symbol:
        return None
    try:
        ensure_fresh(symbol, provider, db, days=days)
        return symbol
    except MarketDataError:
        return None


@router.get("/technical/{symbol}", response_model=TechnicalSnapshot)
def get_technical_snapshot(
    symbol: str,
    days: int = Query(default=365 * 5, gt=0, le=3650),
    provider: MarketDataProvider = Depends(get_market_data_provider),
    db: Session = Depends(get_db),
):
    _ensure_fresh(provider, symbol, db, days)
    results = build_gold(
        symbol, db, requested_products={GoldProduct.TECHNICAL}, provider_name=type(provider).__name__
    )
    return results[GoldProduct.TECHNICAL]


@router.get("/{symbol}/market-structure", response_model=MarketStructureSnapshot)
def get_market_structure_snapshot(
    symbol: str,
    days: int = Query(default=365 * 5, gt=0, le=3650),
    provider: MarketDataProvider = Depends(get_market_data_provider),
    db: Session = Depends(get_db),
):
    _ensure_fresh(provider, symbol, db, days)
    results = build_gold(
        symbol, db, requested_products={GoldProduct.MARKET_STRUCTURE}, provider_name=type(provider).__name__
    )
    return results[GoldProduct.MARKET_STRUCTURE]


@router.get("/{symbol}/risk", response_model=RiskSnapshot)
def get_risk_snapshot(
    symbol: str,
    days: int = Query(default=365 * 5, gt=0, le=3650),
    benchmark: str | None = Query(default="SPY"),
    provider: MarketDataProvider = Depends(get_market_data_provider),
    db: Session = Depends(get_db),
):
    _ensure_fresh(provider, symbol, db, days)
    resolved_benchmark = _ensure_fresh_optional(provider, benchmark, db, days)

    results = build_gold(
        symbol,
        db,
        requested_products={GoldProduct.RISK},
        benchmark_symbol=resolved_benchmark,
        provider_name=type(provider).__name__,
    )
    snapshot = results[GoldProduct.RISK]
    if benchmark and not resolved_benchmark:
        # Beta/correlation just come back "not_supported" instead of
        # blocking the rest of the risk snapshot.
        snapshot.warnings.append(f"Benchmark {benchmark!r} unavailable.")
    return snapshot


@router.get("/{symbol}/volume-liquidity", response_model=VolumeLiquiditySnapshot)
def get_volume_liquidity_snapshot(
    symbol: str,
    days: int = Query(default=365 * 5, gt=0, le=3650),
    provider: MarketDataProvider = Depends(get_market_data_provider),
    db: Session = Depends(get_db),
):
    _ensure_fresh(provider, symbol, db, days)
    results = build_gold(
        symbol, db, requested_products={GoldProduct.VOLUME_LIQUIDITY}, provider_name=type(provider).__name__
    )
    return results[GoldProduct.VOLUME_LIQUIDITY]


@router.get("/{symbol}/market-context", response_model=MarketContextSnapshot)
def get_market_context_snapshot(
    symbol: str,
    days: int = Query(default=365 * 5, gt=0, le=3650),
    market: str | None = Query(default="SPY"),
    sector: str | None = Query(default=None, description="Sector name, e.g. 'Technology' - resolved to a sector ETF via SECTOR_ETF_MAP."),
    provider: MarketDataProvider = Depends(get_market_data_provider),
    db: Session = Depends(get_db),
):
    _ensure_fresh(provider, symbol, db, days)
    warnings: list[str] = []

    resolved_market = _ensure_fresh_optional(provider, market, db, days)
    if market and not resolved_market:
        warnings.append(f"Market benchmark {market!r} unavailable.")

    sector_symbol = SECTOR_ETF_MAP.get(sector) if sector else None
    if sector and not sector_symbol:
        warnings.append(f"Sector {sector!r} isn't mapped to a sector ETF; sector-relative metrics omitted.")
    resolved_sector = _ensure_fresh_optional(provider, sector_symbol, db, days)
    if sector_symbol and not resolved_sector:
        warnings.append(f"Sector benchmark {sector_symbol!r} unavailable.")

    results = build_gold(
        symbol,
        db,
        requested_products={GoldProduct.MARKET_CONTEXT},
        market_symbol=resolved_market,
        sector_symbol=resolved_sector,
        provider_name=type(provider).__name__,
    )
    snapshot = results[GoldProduct.MARKET_CONTEXT]
    snapshot.warnings.extend(warnings)
    return snapshot
