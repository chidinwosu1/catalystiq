"""Deterministic Rule-Based Opportunity Score (a.k.a. Setup Strength).

A transparent 0-100 technical setup-strength score built ONLY from real,
validated indicators the analysis layer already computes with TA-Lib-style
math. It is NOT a probability of profit, AI confidence, or ML prediction, and
it never converts to a buy/sell instruction.

Design rules (per spec):
  - 100-point weighting across five factors:
      trend 30, momentum 25, volume/liquidity 20, volatility/risk 15,
      market/sector 10.
  - Uses only data available at the calculation timestamp; an in-progress
    (unclosed) candle is excluded before anything is computed.
  - A missing/stale/insufficient input NEVER counts as a bearish zero: the
    owning factor is marked insufficient_data, and (v1) the whole score is
    returned as insufficient_data rather than silently renormalizing.
  - No FRED / behavioral / sentiment / news inputs. Behavioral & sentiment are
    always reported unavailable ("No validated data source").
  - The `ml` block is always present and not_available; the rule-based result
    is preserved so ML products can be added alongside later.

The core `build_opportunity_score(...)` is a pure function (bars in, contract
out, injected clock) so every rule is unit-testable offline.
"""
from __future__ import annotations

import datetime as dt

from catalystiq.analysis.indicators import compute_technical_snapshot
from catalystiq.analysis.market_context import (
    SECTOR_ETF_MAP,
    compute_market_context_snapshot,
)
from catalystiq.analysis.market_structure import compute_market_structure_snapshot
from catalystiq.analysis.risk import compute_risk_snapshot
from catalystiq.analysis.volume_liquidity import compute_volume_liquidity_snapshot
from catalystiq.pipelines.freshness import FreshnessPolicy
from catalystiq.schemas.market_data import OHLCVBar
from catalystiq.schemas.opportunity import (
    FactorScore,
    MlStatus,
    OpportunityScan,
    OpportunityScore,
    UnavailableFactor,
)

FORMULA_VERSION = "opportunity_score_v1"

# Factor -> maximum points. Must total 100 (asserted in tests).
FACTOR_WEIGHTS: dict[str, int] = {
    "trend": 30,
    "momentum": 25,
    "volume_liquidity": 20,
    "volatility_risk": 15,
    "market_sector": 10,
}

# The four factors that are always required for any total score. Market/sector
# is also required in v1 (we prefer insufficient_data over a renormalized score).
_CORE_FACTORS = ("trend", "momentum", "volume_liquidity", "volatility_risk")

_ML_NOT_AVAILABLE = MlStatus(
    status="not_available",
    reason="Validated models have not yet been trained and approved.",
)

# Behavioral / sentiment have no validated, licensed, timestamped source yet.
_ALWAYS_UNAVAILABLE = (
    ("behavioral", "No validated data source"),
    ("sentiment", "No validated data source"),
)


def _band(score: int) -> str:
    if score >= 80:
        return "Strong setup"
    if score >= 65:
        return "Favorable setup"
    if score >= 50:
        return "Mixed / Watch"
    if score >= 35:
        return "Weak setup"
    return "Unfavorable setup"


def _reading_ok(reading) -> bool:
    """A reading is usable only if present, computed, and non-null."""
    return (
        reading is not None
        and getattr(reading, "value", None) is not None
        and getattr(reading, "status", None) not in ("insufficient_data", "not_supported")
    )


def _indicator(snapshot, name):
    for r in snapshot.indicators:
        if r.name == name:
            return r
    return None


def _metric(snapshot, name):
    for r in snapshot.metrics:
        if r.name == name:
            return r
    return None


def _insufficient(name: str, reason: str, inputs: dict) -> FactorScore:
    return FactorScore(
        name=name, score=None, max_score=FACTOR_WEIGHTS[name],
        status="insufficient_data", inputs=inputs, explanation=reason,
        formula_version=FORMULA_VERSION,
    )


# --- Factor scorers (each returns a FactorScore; integer sub-points) --------


def _score_trend(tech, structure) -> FactorScore:
    sma20 = _indicator(tech, "sma_20")
    sma50 = _indicator(tech, "sma_50")
    pvs50 = _indicator(tech, "price_vs_sma_50_pct")
    slope = _indicator(tech, "sma_50_slope_10d_pct")
    ts = structure.trend_structure
    needed = {"sma_20": sma20, "sma_50": sma50, "price_vs_sma_50_pct": pvs50,
              "sma_50_slope_10d_pct": slope}
    if not all(_reading_ok(r) for r in needed.values()) or not _reading_ok(ts):
        return _insufficient("trend", "Missing SMA/structure inputs (insufficient history).", {})
    # Sub-points (sum to 30).
    close_price = _last_close(tech)
    t1 = 7 if (close_price is not None and close_price > sma20.value) else 0
    t2 = 7 if pvs50.value > 0 else 0
    t3 = 6 if sma20.value > sma50.value else 0
    t4 = 5 if slope.value > 0 else 0
    t5 = {"higher_highs_higher_lows": 5, "mixed_structure": 2, "range_bound": 2,
          "lower_highs_lower_lows": 0}.get(ts.value, 0)
    score = t1 + t2 + t3 + t4 + t5
    inputs = {
        "sma_20": round(sma20.value, 4), "sma_50": round(sma50.value, 4),
        "close": close_price, "price_vs_sma_50_pct": round(pvs50.value, 4),
        "sma_50_slope_10d_pct": round(slope.value, 4), "trend_structure": ts.value,
    }
    expl = ("Price vs SMA20/50, MA alignment and 10-day slope, and swing "
            "structure. Higher when price leads a rising, aligned MA stack in an "
            "uptrending structure.")
    return FactorScore(name="trend", score=score, max_score=30, status="available",
                       inputs=inputs, explanation=expl, formula_version=FORMULA_VERSION)


def _score_momentum(tech) -> FactorScore:
    rsi = _indicator(tech, "rsi_14")
    line = _indicator(tech, "macd_line")
    signal = _indicator(tech, "macd_signal")
    hist = _indicator(tech, "macd_histogram")
    if not all(_reading_ok(r) for r in (rsi, line, signal, hist)):
        return _insufficient("momentum", "Missing RSI/MACD inputs (insufficient history).", {})
    r = rsi.value
    if r >= 70:
        m1 = 6  # bullish but overbought -> capped
    elif r >= 55:
        m1 = 10
    elif r >= 50:
        m1 = 8
    elif r >= 45:
        m1 = 5
    elif r >= 30:
        m1 = 3
    else:
        m1 = 1
    m2 = 8 if line.value > signal.value else 0
    m3 = 7 if hist.value > 0 else 0
    score = m1 + m2 + m3
    inputs = {"rsi_14": round(r, 3), "macd_line": round(line.value, 5),
              "macd_signal": round(signal.value, 5), "macd_histogram": round(hist.value, 5)}
    expl = ("RSI level (overbought capped), MACD line/signal crossover, and MACD "
            "histogram sign. Higher for constructive, non-exhausted momentum.")
    return FactorScore(name="momentum", score=score, max_score=25, status="available",
                       inputs=inputs, explanation=expl, formula_version=FORMULA_VERSION)


def _score_volume_liquidity(vol) -> FactorScore:
    liq = vol.liquidity_classification
    conf = _metric(vol, "volume_confirmation_of_price")
    relvol = _metric(vol, "relative_volume_pct")
    if not (_reading_ok(liq) and _reading_ok(conf) and _reading_ok(relvol)):
        return _insufficient("volume_liquidity",
                             "Missing liquidity / volume-confirmation inputs.", {})
    v1 = {"high": 8, "moderate": 6, "low": 3, "very_low": 1}.get(liq.value, 0)
    v2 = {"confirmed_up": 7, "neutral": 4, "divergent_up_weak_volume": 3,
          "confirmed_down": 2, "divergent_down_weak_volume": 1}.get(conf.value, 0)
    rv = relvol.value
    v3 = 5 if rv > 120 else 3 if rv >= 80 else 1
    score = v1 + v2 + v3
    inputs = {"liquidity_classification": liq.value,
              "volume_confirmation_of_price": conf.value,
              "relative_volume_pct": round(rv, 2)}
    expl = ("Liquidity class (dollar-volume), whether volume confirms the price "
            "move, and relative volume. Higher for liquid names with confirming "
            "participation.")
    return FactorScore(name="volume_liquidity", score=score, max_score=20, status="available",
                       inputs=inputs, explanation=expl, formula_version=FORMULA_VERSION)


def _score_volatility_risk(risk) -> FactorScore:
    atr_pct = _metric(risk, "atr_14_pct")
    rvol = _metric(risk, "realized_volatility_20d_annualized_pct")
    if not (_reading_ok(atr_pct) and _reading_ok(rvol)):
        return _insufficient("volatility_risk",
                             "Missing ATR% / realized-volatility inputs.", {})
    a = atr_pct.value
    r1 = 9 if a < 2 else 7 if a < 3.5 else 4 if a < 5 else 2 if a < 7 else 1
    v = rvol.value
    r2 = 6 if v < 20 else 5 if v < 30 else 3 if v < 45 else 1 if v < 70 else 0
    score = r1 + r2
    inputs = {"atr_14_pct": round(a, 3), "realized_volatility_20d_annualized_pct": round(v, 3)}
    expl = ("ATR as % of price and 20-day annualized realized volatility. Higher "
            "for calmer, more tradable conditions; extreme volatility scores low.")
    return FactorScore(name="volatility_risk", score=score, max_score=15, status="available",
                       inputs=inputs, explanation=expl, formula_version=FORMULA_VERSION)


def _score_market_sector(symbol, market_bars, market_symbol, context) -> FactorScore:
    # Market (SPY) direction from its own technicals.
    if not market_bars:
        return _insufficient("market_sector", "Market benchmark data unavailable.", {})
    spy = compute_technical_snapshot(market_symbol or "SPY", market_bars)
    spy_pvs = _indicator(spy, "price_vs_sma_50_pct")
    spy_sma20 = _indicator(spy, "sma_20")
    spy_sma50 = _indicator(spy, "sma_50")
    rs_trend = _metric(context, "relative_strength_trend_vs_sector")
    lead = _metric(context, "leading_or_lagging_vs_sector")
    if not (_reading_ok(spy_pvs) and _reading_ok(spy_sma20) and _reading_ok(spy_sma50)):
        return _insufficient("market_sector", "Market benchmark trend insufficient.", {})
    if not (_reading_ok(rs_trend) and _reading_ok(lead)):
        return _insufficient("market_sector",
                             "Sector relative-strength data unavailable for this security.", {})
    market_up = spy_pvs.value > 0 and spy_sma20.value > spy_sma50.value
    ms1 = 4 if market_up else 0
    ms2 = {"rising": 3, "flat": 1, "falling": 0}.get(rs_trend.value, 0)
    ms3 = {"leading": 3, "in_line": 1, "lagging": 0}.get(lead.value, 0)
    score = ms1 + ms2 + ms3
    inputs = {
        "market_symbol": market_symbol or "SPY",
        "market_direction": "up" if market_up else "down_or_sideways",
        "market_price_vs_sma_50_pct": round(spy_pvs.value, 3),
        "relative_strength_trend_vs_sector": rs_trend.value,
        "leading_or_lagging_vs_sector": lead.value,
    }
    expl = ("Market (SPY) trend, the security's sector relative-strength trend, "
            "and whether the security leads or lags its sector.")
    return FactorScore(name="market_sector", score=score, max_score=10, status="available",
                       inputs=inputs, explanation=expl, formula_version=FORMULA_VERSION)


def _last_close(tech) -> float | None:
    # Reconstruct last close from price_vs_sma_50 + sma_50 when possible; else None.
    sma50 = _indicator(tech, "sma_50")
    pvs = _indicator(tech, "price_vs_sma_50_pct")
    if _reading_ok(sma50) and _reading_ok(pvs):
        return round(sma50.value * (1 + pvs.value / 100.0), 6)
    return None


def _drop_unclosed(bars: list[OHLCVBar], last_closed: dt.date) -> list[OHLCVBar]:
    """Exclude an in-progress (unclosed) candle: any trailing bar dated after
    the most recent fully-closed session."""
    trimmed = list(bars)
    while trimmed and trimmed[-1].date > last_closed:
        trimmed = trimmed[:-1]
    return trimmed


def build_opportunity_score(
    symbol: str,
    bars: list[OHLCVBar],
    *,
    now: dt.datetime,
    market_bars: list[OHLCVBar] | None = None,
    market_symbol: str | None = "SPY",
    sector_bars: list[OHLCVBar] | None = None,
    sector_symbol: str | None = None,
    freshness_policy: FreshnessPolicy | None = None,
) -> OpportunityScore:
    """Pure, offline-testable core. `now` is the calculation timestamp; the
    latest unclosed candle is excluded relative to it."""
    policy = freshness_policy or FreshnessPolicy()
    last_closed = policy.latest_expected_session(now)

    bars = _drop_unclosed(bars, last_closed)
    market_bars = _drop_unclosed(market_bars or [], last_closed) or None
    sector_bars = _drop_unclosed(sector_bars or [], last_closed) or None

    calculated_at = now if now.tzinfo else now.replace(tzinfo=dt.timezone.utc)
    unavailable = [UnavailableFactor(name=n, reason=r) for n, r in _ALWAYS_UNAVAILABLE]
    warnings: list[str] = []

    def _envelope(status, score, label, factors, data_as_of, freshness, reason):
        return OpportunityScore(
            symbol=symbol.upper(), status=status, score_type="rule_based", score=score,
            max_score=100, label=label, formula_version=FORMULA_VERSION,
            calculated_at=calculated_at,
            data_as_of=data_as_of, freshness=freshness,
            factor_coverage=f"{sum(1 for f in factors if f.status == 'available')}/5",
            factors=factors, unavailable_factors=unavailable, warnings=warnings,
            ml=_ML_NOT_AVAILABLE, reason=reason,
        )

    if not bars:
        return _envelope("insufficient_data", None, None, [], None, "unknown",
                         "No closed price history available.")

    data_as_of = dt.datetime.combine(bars[-1].date, dt.time(), tzinfo=dt.timezone.utc)

    # Stale data (missing the latest closed session) cannot produce a current score.
    if bars[-1].date < last_closed:
        warnings.append("Latest data is older than the most recent closed session.")
        return _envelope("insufficient_data", None, None, [], data_as_of, "stale",
                         "Price data is stale (older than the last closed session).")

    # Compute the underlying snapshots from the closed bars only.
    tech = compute_technical_snapshot(symbol, bars)
    structure = compute_market_structure_snapshot(symbol, bars)
    vol = compute_volume_liquidity_snapshot(symbol, bars)
    risk = compute_risk_snapshot(symbol, bars)
    context = compute_market_context_snapshot(
        symbol, bars, market_bars=market_bars, market_symbol=market_symbol,
        sector_bars=sector_bars, sector_symbol=sector_symbol,
    )

    factors = [
        _score_trend(tech, structure),
        _score_momentum(tech),
        _score_volume_liquidity(vol),
        _score_volatility_risk(risk),
        _score_market_sector(symbol, market_bars, market_symbol, context),
    ]
    by_name = {f.name: f for f in factors}

    # Required factors must all be available; otherwise insufficient_data (never
    # a renormalized or zero-filled total).
    missing = [n for n in (*_CORE_FACTORS, "market_sector")
               if by_name[n].status != "available"]
    if missing:
        reason = "Insufficient data for required factor(s): " + ", ".join(missing) + "."
        return _envelope("insufficient_data", None, None, factors, data_as_of, "current", reason)

    total = sum(f.score for f in factors)  # integer sub-points sum exactly
    return _envelope("available", total, _band(total), factors, data_as_of, "current", None)


# --- Orchestrator: fetch fresh data + score (used by the endpoint) ----------


def _resolve_sector_etf(symbol, provider, allow_fundamentals_lookup: bool) -> str | None:
    """Resolve ``symbol``'s sector ETF WITHOUT an unconditional live fetch.

    Order of preference:
      1. Governed static sector data (covers the curated scan universe) - no
         network at all.
      2. Only if not governed AND ``allow_fundamentals_lookup`` is set: the
         governed *cache* (TTL + single-flight + concurrency limit + rate-limit
         cooldown). The scan passes False, so a scan NEVER fetches fundamentals.

    Returns None (sector unavailable) rather than inventing one - the caller
    then records the market/sector factor as insufficient_data."""
    from catalystiq.analysis.sectors import governed_sector_etf
    from catalystiq.providers.market_data import MarketDataError

    etf = governed_sector_etf(symbol)
    if etf is not None:
        return etf
    if not allow_fundamentals_lookup:
        return None
    from catalystiq.providers.fundamentals_cache import get_fundamentals_cached

    try:
        sector_name = get_fundamentals_cached(provider, symbol).sector
    except MarketDataError:
        return None
    return SECTOR_ETF_MAP.get(sector_name) if sector_name else None


def score_symbol(
    symbol, provider, db, now: dt.datetime, *, allow_fundamentals_lookup: bool = True
) -> OpportunityScore:
    """Ensure fresh Silver for the symbol, its market benchmark, and its sector
    ETF, then compute the score. Market/sector fetch failures degrade to an
    unavailable market/sector factor (-> insufficient_data in v1), never a crash.
    A primary-symbol data failure propagates as MarketDataError to the caller.

    Sector is resolved via governed data (or, only when
    ``allow_fundamentals_lookup`` is set, a governed cached fundamentals
    lookup) - never an unconditional per-symbol Yahoo `.info` call."""
    from catalystiq.pipelines.market_price_pipeline import (
        ensure_fresh,
        get_silver_bars,
    )
    from catalystiq.providers.market_data import MarketDataError

    symbol = symbol.upper()
    ensure_fresh(symbol, provider, db)
    bars = get_silver_bars(symbol, db)

    market_symbol = "SPY"
    market_bars: list[OHLCVBar] | None = None
    try:
        ensure_fresh(market_symbol, provider, db)
        market_bars = get_silver_bars(market_symbol, db) or None
    except MarketDataError:
        market_bars = None

    # Resolve the sector ETF WITHOUT an unconditional fundamentals fetch.
    sector_symbol = _resolve_sector_etf(symbol, provider, allow_fundamentals_lookup)
    sector_bars: list[OHLCVBar] | None = None
    if sector_symbol:
        try:
            ensure_fresh(sector_symbol, provider, db)
            sector_bars = get_silver_bars(sector_symbol, db) or None
        except MarketDataError:
            sector_bars = None

    # Scoring is CPU-bound and its longest indicator lookback is ~200 sessions,
    # so cap the bars fed to the scorer. Verified score-identical vs full 5y
    # history (tests), while cutting per-symbol CPU ~3x. Ingestion and other
    # analyses still keep the full Silver history.
    max_bars = _scoring_max_bars()
    bars = bars[-max_bars:]
    if market_bars:
        market_bars = market_bars[-max_bars:]
    if sector_bars:
        sector_bars = sector_bars[-max_bars:]

    return build_opportunity_score(
        symbol, bars, now=now, market_bars=market_bars, market_symbol=market_symbol,
        sector_bars=sector_bars, sector_symbol=sector_symbol,
    )


def _scoring_max_bars() -> int:
    from catalystiq.config import get_settings

    return get_settings().scoring_max_bars


# Curated, liquid large-cap starter universe for the ranked scan. This is a
# controlled eligibility list, NOT the whole market - extend/replace as needed.
# (A full constituent universe would need a screened, maintained symbol source.)
SCAN_UNIVERSE: tuple[str, ...] = (
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "AVGO", "TSLA",
    "JPM", "V", "MA", "BAC", "UNH", "JNJ", "LLY", "ABBV",
    "XOM", "CVX", "WMT", "COST", "PG", "KO", "PEP", "HD",
)

_MAX_SCAN_TOP = 10


def scan_universe(provider, db, now: dt.datetime, top: int = 4, universe=None) -> OpportunityScan:
    """Score every symbol in the universe with the rule-based engine, keep only
    the eligible (status=available) ones, rank by score desc, and return the top
    N. A symbol whose data can't be fetched or isn't eligible is skipped - it is
    NEVER replaced with mock or fabricated data."""
    from catalystiq.providers.market_data import MarketDataError

    symbols = tuple(universe) if universe else SCAN_UNIVERSE
    top = max(0, min(top, _MAX_SCAN_TOP))

    eligible: list[OpportunityScore] = []
    for symbol in symbols:
        try:
            # allow_fundamentals_lookup=False: the scan resolves sector from
            # governed data only and never issues a per-symbol Yahoo `.info`
            # call. A symbol with no governed sector degrades to
            # insufficient_data (skipped below), never a fabricated sector.
            result = score_symbol(symbol, provider, db, now, allow_fundamentals_lookup=False)
        except MarketDataError:
            continue  # unfetchable -> skip, never mock-fill
        if result.status == "available" and result.score is not None:
            eligible.append(result)

    eligible.sort(key=lambda r: (-(r.score or 0), r.symbol))
    # NOTE: entry_quality is intentionally NOT attached here. It is served
    # independently by the per-symbol /entry-quality endpoint that the Trade
    # Center cards poll every 15s, so keeping it off the scan's (background)
    # warm path means candidates render as soon as the daily scoring completes -
    # the intraday fetch never delays a card appearing. Each candidate carries
    # entry_quality=None; the card fills it in on first poll.
    candidates = eligible[:top]
    return OpportunityScan(
        as_of=now if now.tzinfo else now.replace(tzinfo=dt.timezone.utc),
        formula_version=FORMULA_VERSION,
        universe_size=len(symbols),
        eligible_count=len(eligible),
        top=top,
        candidates=candidates,
        ml=_ML_NOT_AVAILABLE,
        note=None if eligible else "No symbols currently meet the rule-based eligibility criteria.",
    )


# --- Short-TTL scan cache ---------------------------------------------------
# A cold scan is expensive (it may ingest history for the whole universe); a
# warm one is still a full scoring loop. This cache lets repeated / concurrent
# scan requests reuse one computed result within a short window. The cached
# OpportunityScan keeps its original as_of (point-in-time), never re-stamped.

import logging as _logging  # noqa: E402
import threading as _threading  # noqa: E402
import time as _time  # noqa: E402
from dataclasses import dataclass as _dataclass  # noqa: E402

_logger = _logging.getLogger(__name__)


@_dataclass
class _ScanCacheEntry:
    scan: OpportunityScan
    stored_at: float


_SCAN_CACHE: dict[tuple, _ScanCacheEntry] = {}
_SCAN_CACHE_LOCK = _threading.Lock()


def clear_scan_cache() -> None:
    """Drop cached scans. Test-support only."""
    with _SCAN_CACHE_LOCK:
        _SCAN_CACHE.clear()


def refresh_scan_cache(provider, db, now: dt.datetime, tops=(4,), monotonic=_time.monotonic) -> None:
    """Compute the default-universe scan for each `top` and store it in the
    cache. Called by the background warmer so the user-facing scan request is a
    pure cache read instead of a ~tens-of-seconds scoring loop. A failure for
    one `top` is logged and skipped - it never breaks the warm loop."""
    for top in tops:
        try:
            scan = scan_universe(provider, db, now, top=top)
        except Exception:  # pragma: no cover - defensive; keep other tops
            _logger.exception("scan cache refresh failed for top=%s", top)
            continue
        key = (SCAN_UNIVERSE, max(0, min(top, _MAX_SCAN_TOP)))
        with _SCAN_CACHE_LOCK:
            _SCAN_CACHE[key] = _ScanCacheEntry(scan=scan, stored_at=monotonic())


def scan_universe_cached(
    provider,
    db,
    now: dt.datetime,
    top: int = 4,
    universe=None,
    *,
    ttl_seconds: float | None = None,
    monotonic=_time.monotonic,
) -> OpportunityScan:
    """`scan_universe` with a short-TTL result cache keyed by (universe, top).
    Within the TTL, a repeat request returns the cached scan (with its original
    as_of) instead of re-running the loop."""
    if ttl_seconds is None:
        from catalystiq.config import get_settings

        ttl_seconds = get_settings().opportunity_scan_cache_ttl_seconds

    symbols = tuple(universe) if universe else SCAN_UNIVERSE
    key = (symbols, max(0, min(top, _MAX_SCAN_TOP)))

    if ttl_seconds > 0:
        with _SCAN_CACHE_LOCK:
            entry = _SCAN_CACHE.get(key)
            if entry is not None and (monotonic() - entry.stored_at) < ttl_seconds:
                return entry.scan

    # Computed outside the lock so a slow scan doesn't block cache reads.
    scan = scan_universe(provider, db, now, top=top, universe=universe)
    if ttl_seconds > 0:
        with _SCAN_CACHE_LOCK:
            _SCAN_CACHE[key] = _ScanCacheEntry(scan=scan, stored_at=monotonic())
    return scan


# --- Non-blocking scan for the request path ---------------------------------
# A cold scan can take tens of seconds (it ingests history for the whole
# universe through a rate-limited provider). Running that on the request thread
# leaves the UI stuck on "Scanning the universe…" indefinitely. Instead the
# user-facing endpoint serves whatever is cached (even slightly stale) and, when
# nothing is cached yet, kicks a SINGLE-FLIGHT background compute and returns a
# fast "warming up" placeholder. The background warmer still refreshes on its
# own schedule; this just guarantees the request never blocks on a cold scan.

_SCAN_INFLIGHT: set[tuple] = set()  # keys with a background compute running


def _warming_scan(now: dt.datetime, top: int, symbols: tuple[str, ...]) -> OpportunityScan:
    """A fast, real (non-mock) OpportunityScan placeholder returned while the
    real scan is still being computed in the background."""
    return OpportunityScan(
        as_of=now if now.tzinfo else now.replace(tzinfo=dt.timezone.utc),
        formula_version=FORMULA_VERSION,
        universe_size=len(symbols),
        eligible_count=0,
        top=max(0, min(top, _MAX_SCAN_TOP)),
        candidates=[],
        ml=_ML_NOT_AVAILABLE,
        note="Opportunity setups are warming up — check back in a moment.",
    )


def _run_background_scan(top: int, universe, key: tuple, monotonic=_time.monotonic) -> None:
    """Compute the scan with a fresh session + provider and cache it. Runs in a
    daemon thread so a cold/slow scan never blocks the request. Always clears the
    in-flight marker, even on failure, so a later request can retry."""
    try:
        from catalystiq.db.base import SessionLocal
        from catalystiq.providers.market_data import get_market_data_provider

        db = SessionLocal()
        try:
            scan = scan_universe(
                get_market_data_provider(),
                db,
                dt.datetime.now(dt.timezone.utc),
                top=top,
                universe=universe,
            )
            with _SCAN_CACHE_LOCK:
                _SCAN_CACHE[key] = _ScanCacheEntry(scan=scan, stored_at=monotonic())
        finally:
            db.close()
    except Exception:  # pragma: no cover - defensive; a failed warm just retries
        _logger.exception("background scan warm failed for %s", key)
    finally:
        with _SCAN_CACHE_LOCK:
            _SCAN_INFLIGHT.discard(key)


def _start_background_scan(top: int, universe, key: tuple) -> None:
    """Spawn the background compute. Isolated so tests can stub it out."""
    _threading.Thread(
        target=_run_background_scan, args=(top, universe, key), daemon=True
    ).start()


def scan_universe_fast(
    now: dt.datetime,
    top: int = 4,
    universe=None,
    *,
    ttl_seconds: float | None = None,
    monotonic=_time.monotonic,
) -> OpportunityScan:
    """Non-blocking scan for the request path. Returns the cached scan if present
    (serving a slightly-stale one rather than blocking on recompute) and kicks a
    single-flight background refresh when the cache is cold or expired; when
    nothing is cached yet, returns a fast "warming up" placeholder. The cold
    ingest/scoring loop never runs on the calling (request) thread."""
    if ttl_seconds is None:
        from catalystiq.config import get_settings

        ttl_seconds = get_settings().opportunity_scan_cache_ttl_seconds

    symbols = tuple(universe) if universe else SCAN_UNIVERSE
    top_c = max(0, min(top, _MAX_SCAN_TOP))
    key = (symbols, top_c)

    with _SCAN_CACHE_LOCK:
        entry = _SCAN_CACHE.get(key)
        is_fresh = (
            entry is not None and ttl_seconds > 0 and (monotonic() - entry.stored_at) < ttl_seconds
        )
        needs_warm = entry is None or not is_fresh
        should_start = needs_warm and key not in _SCAN_INFLIGHT
        if should_start:
            _SCAN_INFLIGHT.add(key)

    if should_start:
        _start_background_scan(top, universe, key)

    # Prefer a real (possibly stale) scan over the placeholder; the background
    # refresh above will replace it shortly.
    if entry is not None:
        return entry.scan
    return _warming_scan(now, top_c, symbols)


def scan_cache_debug(monotonic=_time.monotonic) -> dict:
    """Read-only snapshot of the scan cache / in-flight state for diagnostics.
    Explains WHY the Trade Center may be empty: is a real scan cached, how many
    candidates / eligible symbols did it find, and is a background warm still
    running? Never triggers a scan or mutates state."""
    with _SCAN_CACHE_LOCK:
        entries = []
        for (symbols, top), e in _SCAN_CACHE.items():
            entries.append({
                "universe_size": len(symbols),
                "top": top,
                "age_seconds": round(monotonic() - e.stored_at, 1),
                "candidate_count": len(e.scan.candidates),
                "eligible_count": e.scan.eligible_count,
                "note": e.scan.note,
                "as_of": e.scan.as_of.isoformat() if e.scan.as_of else None,
            })
        in_flight = len(_SCAN_INFLIGHT)
    return {
        "cached_scans": entries,
        "background_warm_in_flight": in_flight,
        "is_warming": in_flight > 0 and not any(x["candidate_count"] for x in entries),
    }
