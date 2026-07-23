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

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from catalystiq.analysis.market_context import SECTOR_ETF_MAP
from catalystiq.analysis.entry_quality import resolve_entry_quality
from catalystiq.analysis.opportunity_score import (
    scan_universe_cached,
    scan_universe_fast,
    score_symbol,
)
from catalystiq.auth import verify_action_key
from catalystiq.db.base import get_db
from catalystiq.pipelines.market_price_pipeline import GoldProduct, build_gold, ensure_fresh
from catalystiq.providers.market_data import (
    MarketDataError,
    MarketDataProvider,
    get_market_data_provider,
)
from catalystiq.schemas.analysis import TechnicalSnapshot
from catalystiq.schemas.diagnostics import MarketDataDiagnostics, ProviderProbe
from catalystiq.schemas.entry_quality import EntryQualityScore
from catalystiq.schemas.opportunity import OpportunityScan, OpportunityScore
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


@router.get("/opportunity-scan", response_model=OpportunityScan)
def get_opportunity_scan(
    top: int = Query(default=4, gt=0, le=10),
    symbols: str | None = Query(
        default=None, description="Optional universe override (comma-separated symbols)."
    ),
    provider: MarketDataProvider = Depends(get_market_data_provider),
    db: Session = Depends(get_db),
):
    """Scan a curated eligible universe, score each symbol with the rule-based
    engine, and return the top-N ranked candidates (only fully-eligible
    'available' scores qualify; unfetchable/ineligible symbols are skipped, never
    mock-filled)."""
    now = dt.datetime.now(dt.timezone.utc)
    if symbols:
        # Explicit ad-hoc universe: no background warmer backs it, so compute
        # inline (cached) as before.
        universe = [s.strip().upper() for s in symbols.split(",") if s.strip()]
        return scan_universe_cached(provider, db, now=now, top=top, universe=universe)
    # Default universe: never block the request on a cold scan. Serve cache (even
    # slightly stale) and warm in the background, returning a fast "warming up"
    # placeholder only when nothing is cached yet. Prevents the UI from hanging
    # on "Scanning the universe…".
    return scan_universe_fast(now, top=top)


@router.get("/{symbol}/opportunity-score", response_model=OpportunityScore)
def get_opportunity_score(
    symbol: str,
    provider: MarketDataProvider = Depends(get_market_data_provider),
    db: Session = Depends(get_db),
):
    """Deterministic Rule-Based Opportunity Score (Setup Strength) - a
    transparent 0-100 technical setup-strength read, NOT a probability of
    profit or an ML/AI prediction, and never a buy/sell instruction. Returns
    status "insufficient_data" (never a guessed or renormalized number) when a
    required factor is missing, stale, or lacks history."""
    now = dt.datetime.now(dt.timezone.utc)
    try:
        score = score_symbol(symbol, provider, db, now=now)
    except MarketDataError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    # Attach the INDEPENDENT real-time Entry Quality Score from the DEDICATED
    # intraday provider (Webull real-time when configured, else Yahoo) - best-
    # effort; degrades to insufficient_data when no intraday feed is available.
    setup_is_strong = score.label in ("Strong setup", "Favorable setup")
    eq = resolve_entry_quality(symbol, now, setup_is_strong=setup_is_strong)
    return score.model_copy(update={"entry_quality": eq})


@router.get("/{symbol}/entry-quality", response_model=EntryQualityScore)
def get_entry_quality_score(symbol: str):
    """Dynamic Entry Quality Score (0-100) - a real-time, intraday read of
    whether the *current moment* is an attractive entry, INDEPENDENT of the
    daily Setup Strength. Served from the DEDICATED intraday provider (Webull
    real-time when configured, else Yahoo). Returns status "insufficient_data"
    (never a guessed number) when intraday inputs are missing, stale, or the
    provider has no intraday feed. This is the endpoint the Trade Center cards
    and Entry Check pop-out poll every 15 seconds."""
    return resolve_entry_quality(symbol, dt.datetime.now(dt.timezone.utc))


_DIAGNOSTIC_CANARY = "SPY"


def _probe_provider(factory, symbol: str, *, intraday: bool) -> ProviderProbe:
    """Live health probe of a market-data provider. Constructs it (catching a
    missing-credential / import failure), then makes ONE light, gated call
    (intraday bars or a quote) and classifies any failure as rate-limited or
    not. Never raises - a probe failure IS the diagnostic result."""
    import time as _t

    from catalystiq.providers.fundamentals_cache import is_rate_limited_error
    from catalystiq.providers.market_data_gate import get_gate_for

    t0 = _t.perf_counter()
    try:
        provider = factory()
    except Exception as exc:  # provider could not even be constructed
        return ProviderProbe(
            provider="unavailable", symbol=symbol, ok=False,
            rate_limited=is_rate_limited_error(exc), detail=str(exc)[:300],
            latency_ms=int((_t.perf_counter() - t0) * 1000),
        )

    name = getattr(provider, "PROVIDER_NAME", type(provider).__name__)
    try:
        if intraday and callable(getattr(provider, "get_intraday_ohlcv", None)):
            bars = get_gate_for(provider).run(
                f"probe-intraday {symbol}",
                lambda: provider.get_intraday_ohlcv(symbol, interval="5m", days=1),
            )
            ok, detail = bool(bars), (
                f"fetched {len(bars)} intraday bars" if bars else "no intraday bars returned"
            )
        else:
            quote = get_gate_for(provider).run(
                f"probe-quote {symbol}", lambda: provider.get_quote(symbol)
            )
            ok, detail = True, f"quote ok: ${quote.price:.2f}"
        rate_limited = False
    except Exception as exc:
        ok, rate_limited, detail = False, is_rate_limited_error(exc), str(exc)[:300]
    return ProviderProbe(
        provider=name, symbol=symbol, ok=ok, rate_limited=rate_limited,
        detail=detail, latency_ms=int((_t.perf_counter() - t0) * 1000),
    )


@router.get("/diagnostics/market-data", response_model=MarketDataDiagnostics)
def get_market_data_diagnostics(
    symbol: str = Query(default=_DIAGNOSTIC_CANARY, description="Canary symbol to probe."),
):
    """One-call market-data health check that explains WHY the Trade Center may
    be empty. Probes the daily (Setup Strength / scan) provider and the intraday
    (Entry Check) provider live, reports the rate-limit gate counters and the
    scan-cache state, and summarizes the likely cause. Makes two light,
    gated provider calls; safe to hit on demand."""
    from catalystiq.analysis.opportunity_score import scan_cache_debug
    from catalystiq.config import get_settings
    from catalystiq.providers.market_data import (
        get_intraday_market_data_provider,
        get_market_data_provider,
    )
    from catalystiq.providers.market_data_gate import market_data_gate_stats

    settings = get_settings()
    daily = _probe_provider(get_market_data_provider, symbol, intraday=False)
    intraday = _probe_provider(get_intraday_market_data_provider, symbol, intraday=True)
    scan_cache = scan_cache_debug()

    # Summarize the likely cause, most-actionable first.
    if daily.rate_limited or any(
        s.get("rate_limited", 0) or s.get("cooldown_short_circuits", 0)
        for s in market_data_gate_stats().values()
    ):
        summary = (
            "Upstream rate limit detected on the daily provider - the scan can't "
            "fetch history, so no candidates appear. This is a provider (Yahoo) "
            "per-IP throttle, not the Entry Check code."
        )
    elif not daily.ok:
        summary = f"Daily provider unreachable ({daily.detail}); the scan cannot produce candidates."
    elif any(e["candidate_count"] for e in scan_cache["cached_scans"]):
        summary = "Scan is healthy - candidates are cached and should render."
    elif scan_cache["background_warm_in_flight"]:
        summary = "Scan is still warming (background compute in flight); no candidates cached yet."
    else:
        summary = (
            "Daily provider reachable but the scan has no eligible candidates cached yet - "
            "trigger a scan (open Trade Center) and re-check."
        )

    return MarketDataDiagnostics(
        checked_at=dt.datetime.now(dt.timezone.utc),
        config={
            "market_data_provider": settings.market_data_provider,
            "intraday_market_data_provider": settings.intraday_market_data_provider,
            "webull_market_data_configured": bool(
                settings.webull_app_key and settings.webull_app_secret
            ),
        },
        daily_provider_probe=daily,
        intraday_provider_probe=intraday,
        gate_stats=market_data_gate_stats(),
        scan_cache=scan_cache,
        summary=summary,
    )


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
