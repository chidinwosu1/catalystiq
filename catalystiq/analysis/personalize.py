"""Preference-aware personalization of the opportunity scan.

The rule-based :class:`OpportunityScore` is preference-agnostic: it measures
*long* setup strength on a daily timeframe with fixed factor weights. That is
why the Trade Center's "Highest-conviction setups" returned the same top names
regardless of the preferences a user submitted - the preferences never reached
the scan, and even if they had, nothing here consumed them.

This module is the missing consumer. Given the already-computed per-symbol
scores (the expensive, cacheable, preference-independent part) plus a
:class:`ScanPreferences`, it deterministically:

  * filters the eligible set by **asset class**, **direction**, **risk / max
    loss** (volatility eligibility), and **affordability**;
  * re-scores each survivor with **holding-period-appropriate factor weights**
    (and direction-aware factor mirroring for short setups);
  * computes **position sizing** from the investable amount and max loss.

It is a pure function of (scores, preferences) - no I/O, no randomness - so the
same inputs always produce the same ranked output, and every step is unit
testable offline. Nothing is ever fabricated or back-filled: a survivor set of
zero yields an honest empty scan, never a fall back to a fixed list of names.
"""
from __future__ import annotations

import datetime as dt
import math

from catalystiq.analysis.sectors import asset_class
from catalystiq.schemas.opportunity import (
    MlStatus,
    OpportunityScan,
    OpportunityScore,
    PersonalizationInfo,
    ScanPreferences,
)

# --- Holding-period scoring weights ----------------------------------------
# Each style redistributes the 100 points across the five factors to match what
# matters over that horizon: intraday/day trading leans on momentum, volume and
# tradable volatility; long-term leans on trend and market/sector context. Each
# row sums to 100, so the personalized score stays on the same 0-100 scale.
# "swing" is intentionally identical to the base FACTOR_WEIGHTS, so a swing
# profile reproduces the generic ranking exactly (a useful invariant in tests).
STYLE_WEIGHTS: dict[str, dict[str, int]] = {
    "intraday": {"trend": 15, "momentum": 30, "volume_liquidity": 30,
                 "volatility_risk": 20, "market_sector": 5},
    "day":      {"trend": 20, "momentum": 28, "volume_liquidity": 27,
                 "volatility_risk": 18, "market_sector": 7},
    "swing":    {"trend": 30, "momentum": 25, "volume_liquidity": 20,
                 "volatility_risk": 15, "market_sector": 10},
    "long":     {"trend": 38, "momentum": 15, "volume_liquidity": 12,
                 "volatility_risk": 15, "market_sector": 20},
}

# Factors whose meaning flips for a short setup. For a short, a WEAK long-trend
# (low trend/momentum/market fraction) is a STRONG short (fraction is mirrored to
# 1 - f). Volume/liquidity (tradability) and volatility_risk (calmness) read the
# same regardless of direction, so they are not mirrored.
_DIRECTIONAL_FACTORS = ("trend", "momentum", "market_sector")

# --- Risk tolerance -> volatility ceilings ---------------------------------
# A more conservative trader tolerates less volatility. Values in % (ATR as % of
# price; realized vol annualized %). A candidate above the ceiling for the
# chosen risk tolerance is ineligible on risk grounds.
RISK_ATR_CEIL: dict[str, float] = {"conservative": 3.5, "moderate": 5.5, "aggressive": 9.0}
RISK_RVOL_CEIL: dict[str, float] = {"conservative": 30.0, "moderate": 45.0, "aggressive": 80.0}

# --- Holding-period -> stop distance (in ATRs) -----------------------------
# The protective stop's distance from entry, expressed in ATRs, scales with the
# horizon: intraday stops are tight fractions of the daily range; long-term
# stops are wide. stop_distance_pct = mult * atr_pct. A setup is only affordable
# within the user's max-loss budget when stop_distance_pct <= max_loss_pct.
STYLE_STOP_ATR_MULT: dict[str, float] = {
    "intraday": 0.6, "day": 0.8, "swing": 1.5, "long": 3.0,
}

_ML_NOT_AVAILABLE = MlStatus(
    status="not_available",
    reason="Validated models have not yet been trained and approved.",
)


def _factor(score: OpportunityScore, name: str):
    for f in score.factors:
        if f.name == name:
            return f
    return None


def _factor_input(score: OpportunityScore, factor: str, key: str):
    f = _factor(score, factor)
    if f is None:
        return None
    return f.inputs.get(key)


def reference_price(score: OpportunityScore) -> float | None:
    """The last closed price for sizing. Taken from the trend factor's inputs;
    reconstructed from SMA50 * (1 + price_vs_sma50%) as a fallback."""
    close = _factor_input(score, "trend", "close")
    if isinstance(close, (int, float)):
        return float(close)
    sma50 = _factor_input(score, "trend", "sma_50")
    pvs = _factor_input(score, "trend", "price_vs_sma_50_pct")
    if isinstance(sma50, (int, float)) and isinstance(pvs, (int, float)):
        return round(float(sma50) * (1 + float(pvs) / 100.0), 6)
    return None


def classify_direction(score: OpportunityScore) -> str:
    """The directional bias of the setup from its trend inputs.

    Bullish (price above SMA50 and SMA20 above SMA50) -> "long".
    Bearish (price below SMA50 and SMA20 below SMA50) -> "short".
    A mixed/neutral structure defaults to "long" (the score measures long setup
    strength), so it is only ever offered as a long candidate.
    """
    pvs = _factor_input(score, "trend", "price_vs_sma_50_pct")
    sma20 = _factor_input(score, "trend", "sma_20")
    sma50 = _factor_input(score, "trend", "sma_50")
    if not all(isinstance(x, (int, float)) for x in (pvs, sma20, sma50)):
        return "long"
    if pvs > 0 and sma20 > sma50:
        return "long"
    if pvs < 0 and sma20 < sma50:
        return "short"
    return "long"


def _volatility(score: OpportunityScore) -> tuple[float | None, float | None]:
    atr = _factor_input(score, "volatility_risk", "atr_14_pct")
    rvol = _factor_input(score, "volatility_risk", "realized_volatility_20d_annualized_pct")
    atr_f = float(atr) if isinstance(atr, (int, float)) else None
    rvol_f = float(rvol) if isinstance(rvol, (int, float)) else None
    return atr_f, rvol_f


def personalized_score(score: OpportunityScore, style: str, direction: str) -> int:
    """Re-weight the factor sub-scores for the holding period, mirroring the
    directional factors for a short setup. Returns an integer 0-100."""
    weights = STYLE_WEIGHTS.get(style, STYLE_WEIGHTS["swing"])
    total = 0.0
    for f in score.factors:
        w = weights.get(f.name)
        if w is None or f.score is None or f.max_score <= 0:
            continue
        frac = f.score / f.max_score
        if direction == "short" and f.name in _DIRECTIONAL_FACTORS:
            frac = 1.0 - frac
        total += frac * w
    return int(round(max(0.0, min(100.0, total))))


def _risk_ok(atr_pct: float | None, rvol_pct: float | None, risk: str) -> bool:
    """Volatility eligibility for the chosen risk tolerance. Missing volatility
    inputs are disqualifying (we never assume a name is calm enough)."""
    if atr_pct is None or rvol_pct is None:
        return False
    return atr_pct <= RISK_ATR_CEIL[risk] and rvol_pct <= RISK_RVOL_CEIL[risk]


def stop_distance_pct(atr_pct: float | None, style: str) -> float | None:
    if atr_pct is None:
        return None
    return round(STYLE_STOP_ATR_MULT.get(style, 1.5) * atr_pct, 4)


def _max_loss_ok(stop_pct: float | None, max_loss_pct: float) -> bool:
    """A setup fits the risk budget only when a protective stop at the
    style-scaled ATR distance loses no more than the user's max acceptable loss.
    Missing volatility (stop unknown) is disqualifying."""
    if stop_pct is None:
        return False
    # Tiny epsilon so an exact match (e.g. stop 5.00% vs max 5%) is allowed.
    return stop_pct <= max_loss_pct + 1e-9


def position_sizing(
    price: float | None, stop_pct: float | None, prefs: ScanPreferences
) -> tuple[float | None, float | None, float | None, bool]:
    """Size a position from investable capital and max loss.

    Shares are the smaller of (a) risk-based sizing - the share count whose loss
    at the stop equals the user's max-loss budget in dollars - and (b) what the
    capital can buy outright. With fractional shares the position is never
    rejected for a high price; without them a name the capital can't buy a whole
    share of is unaffordable.

    Returns (shares, position_value, est_max_loss_dollars, affordable).
    """
    if price is None or price <= 0 or stop_pct is None or stop_pct <= 0:
        return None, None, None, False
    stop_dollars = price * stop_pct / 100.0
    risk_budget = prefs.amount * prefs.max_loss_pct / 100.0
    risk_shares = risk_budget / stop_dollars if stop_dollars > 0 else 0.0
    cap_shares = prefs.amount / price
    shares = min(risk_shares, cap_shares)
    if not prefs.fractional_shares:
        shares = float(math.floor(shares))
        if shares < 1:
            return 0.0, 0.0, 0.0, False
    else:
        # Fractional support: never reject on price. Still guard a degenerate
        # zero-capital case.
        if shares <= 0:
            return 0.0, 0.0, 0.0, prefs.amount > 0
    position_value = round(shares * price, 2)
    est_max_loss = round(shares * stop_dollars, 2)
    return round(shares, 4), position_value, est_max_loss, True


def personalize_scan(
    scored: list[OpportunityScore],
    prefs: ScanPreferences,
    *,
    now: dt.datetime,
    universe_size: int,
    top: int,
    data_outage: bool = False,
    outage_note: str | None = None,
) -> OpportunityScan:
    """Filter, size, re-rank and take the top-N of ``scored`` for ``prefs``.

    ``scored`` are the eligible (status="available") rule-based scores for the
    universe. ``data_outage`` / ``outage_note`` propagate an honest "market data
    unavailable" state from the underlying scan so an empty result is never
    mistaken for "nothing matched your preferences".
    """
    as_of = now if now.tzinfo else now.replace(tzinfo=dt.timezone.utc)

    def _envelope(candidates, note, status):
        return OpportunityScan(
            as_of=as_of, formula_version=(scored[0].formula_version if scored else "opportunity_score_v1"),
            universe_size=universe_size, eligible_count=len(candidates), top=top,
            candidates=candidates, ml=_ML_NOT_AVAILABLE, note=note, status=status,
        )

    if data_outage:
        return _envelope([], outage_note or "Market data is temporarily unavailable; "
                         "the scan will keep retrying automatically.", "unavailable")

    allowed_assets = set(prefs.assets)
    survivors: list[OpportunityScore] = []
    for score in scored:
        if score.status != "available" or score.score is None:
            continue
        # 1. Asset class.
        cls = asset_class(score.symbol)
        if allowed_assets and cls not in allowed_assets:
            continue
        # 2. Direction.
        direction = classify_direction(score)
        if prefs.direction == "long" and direction != "long":
            continue
        # 3. Risk tolerance (volatility ceilings).
        atr_pct, rvol_pct = _volatility(score)
        if not _risk_ok(atr_pct, rvol_pct, prefs.risk):
            continue
        # 4. Max acceptable loss (style-scaled ATR stop must fit the budget).
        stop_pct = stop_distance_pct(atr_pct, prefs.style)
        if not _max_loss_ok(stop_pct, prefs.max_loss_pct):
            continue
        # 5. Affordability / position sizing.
        price = reference_price(score)
        shares, pos_value, est_loss, affordable = position_sizing(price, stop_pct, prefs)
        if not affordable:
            continue

        pscore = personalized_score(score, prefs.style, direction)
        applied = [
            f"style={prefs.style} weighting",
            f"risk={prefs.risk} volatility ceiling",
            f"max_loss={prefs.max_loss_pct}% vs stop {stop_pct}%",
            f"direction={direction}",
            f"asset_class={cls}",
        ]
        info = PersonalizationInfo(
            direction=direction, asset_class=cls, reference_price=price,
            base_score=int(score.score), personalized_score=pscore,
            atr_pct=atr_pct, stop_distance_pct=stop_pct, est_shares=shares,
            est_position_value=pos_value, est_max_loss=est_loss, applied=applied,
        )
        survivors.append(score.model_copy(update={"personalization": info}))

    # Rank by the personalized score (desc), then base score, then symbol - fully
    # deterministic, no randomness.
    survivors.sort(
        key=lambda s: (
            -(s.personalization.personalized_score if s.personalization else 0),
            -(s.score or 0),
            s.symbol,
        )
    )
    candidates = survivors[:top]

    if not candidates:
        note = ("No setups in the scanned universe match your preferences "
                "(holding period, risk tolerance, max loss, direction, or asset "
                "class). Try widening your risk tolerance or max acceptable loss.")
        return _envelope([], note, "ok")
    return _envelope(candidates, None, "ok")
