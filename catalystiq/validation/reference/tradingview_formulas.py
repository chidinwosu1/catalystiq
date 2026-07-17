"""Independent reference implementations of standard technical indicators
that TA-Lib doesn't carry, following each indicator's published TradingView
formula. These are real, standard, widely-used indicators - the absence of
a TA-Lib function for them is not evidence they're "proprietary" - they're
just outside TA-Lib's specific function set.

Each function is a genuinely separate implementation from Catalyst IQ's own
(catalystiq/analysis/volume_liquidity.py, indicators.py, market_structure.py):
numpy directly rather than pandas rolling windows, so a bug shared between
"the formula as coded" and "the formula as reference-checked" is much less
likely than if this just re-imported the original function.

Uses the SAME windowing/return conventions Catalyst IQ's own modules
already document (e.g. Relative Volume's prior-N-bars-excluding-today
average) - the point of reference validation is catching code bugs, not
adjudicating between equally legitimate convention choices, so inputs and
parameters must match exactly per indicator before comparing (module
docstrings note each convention explicitly).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class ReferenceValue:
    value: float | None
    formula: str


@dataclass(frozen=True)
class PivotPoint:
    index: int
    price: float


@dataclass(frozen=True)
class PivotResult:
    highs: list[PivotPoint]
    lows: list[PivotPoint]
    formula: str


_RELATIVE_VOLUME_FORMULA = (
    "Relative Volume = volume[today] / SMA(volume, N bars prior to today) * 100"
)


def relative_volume(volume: np.ndarray, window: int) -> ReferenceValue:
    """TradingView-standard Relative Volume, using Catalyst IQ's own
    documented "prior N bars, excluding today" averaging window
    (catalystiq/analysis/volume_liquidity.py's RELATIVE_VOLUME_WINDOW)."""
    if len(volume) <= window:
        return ReferenceValue(None, _RELATIVE_VOLUME_FORMULA)
    prior_avg = float(np.mean(volume[-1 - window : -1]))
    if prior_avg <= 0:
        return ReferenceValue(None, _RELATIVE_VOLUME_FORMULA)
    return ReferenceValue(float(volume[-1]) / prior_avg * 100.0, _RELATIVE_VOLUME_FORMULA)


_CMF_FORMULA = (
    "Chaikin Money Flow = sum(((close-low)-(high-close))/(high-low) * volume, N) / sum(volume, N)"
)


def chaikin_money_flow(
    high: np.ndarray, low: np.ndarray, close: np.ndarray, volume: np.ndarray, period: int
) -> ReferenceValue:
    if len(close) < period:
        return ReferenceValue(None, _CMF_FORMULA)

    h = high[-period:]
    l = low[-period:]
    c = close[-period:]
    v = volume[-period:]
    rng = h - l
    with np.errstate(divide="ignore", invalid="ignore"):
        mfm = np.where(rng != 0, ((c - l) - (h - c)) / rng, 0.0)
    mfv = mfm * v
    total_volume = float(np.sum(v))
    if total_volume == 0:
        return ReferenceValue(None, _CMF_FORMULA)
    return ReferenceValue(float(np.sum(mfv)) / total_volume, _CMF_FORMULA)


_PVT_FORMULA = "PVT[i] = PVT[i-1] + volume[i] * (close[i] - close[i-1]) / close[i-1], cumulative"


def price_volume_trend(close: np.ndarray, volume: np.ndarray) -> ReferenceValue:
    if len(close) < 2:
        return ReferenceValue(None, _PVT_FORMULA)
    prior = close[:-1]
    curr = close[1:]
    vol = volume[1:]
    with np.errstate(divide="ignore", invalid="ignore"):
        pct_change = np.where(prior != 0, (curr - prior) / prior, 0.0)
    pvt = float(np.sum(vol * pct_change))
    return ReferenceValue(pvt, _PVT_FORMULA)


_HISTORICAL_VOLATILITY_FORMULA = (
    "Historical Volatility = stdev(ln(close[i]/close[i-1]), N) * sqrt(252) * 100"
)


def historical_volatility(close: np.ndarray, window: int, annualization_days: int = 252) -> ReferenceValue:
    if len(close) < window + 1:
        return ReferenceValue(None, _HISTORICAL_VOLATILITY_FORMULA)
    log_returns = np.diff(np.log(close))
    recent = log_returns[-window:]
    stdev = float(np.std(recent, ddof=1))
    return ReferenceValue(stdev * (annualization_days**0.5) * 100.0, _HISTORICAL_VOLATILITY_FORMULA)


_PIVOT_FORMULA = (
    "Pivot High/Low (fractal): bar i is a pivot high if high[i] is strictly greater than "
    "every high within leftbars before and rightbars after (mirror for pivot low) - "
    "TradingView Pine Script ta.pivothigh()/ta.pivotlow() definition"
)


def pivot_points(high: np.ndarray, low: np.ndarray, leftbars: int, rightbars: int) -> PivotResult:
    """Independent fractal pivot detection, same left/right-bar convention
    as catalystiq/analysis/market_structure.py's _swing_points(). Only
    emits *confirmed* pivots (a full rightbars window available) - an
    unconfirmed candidate at the series' tail can still change as more
    data arrives, so it isn't a meaningful thing to reference-compare."""
    n = len(high)
    highs: list[PivotPoint] = []
    lows: list[PivotPoint] = []

    for i in range(leftbars, n - rightbars):
        window_start = i - leftbars
        window_end = i + rightbars + 1

        neighborhood_high = np.concatenate([high[window_start:i], high[i + 1 : window_end]])
        if neighborhood_high.size > 0 and high[i] > neighborhood_high.max():
            highs.append(PivotPoint(index=i, price=float(high[i])))

        neighborhood_low = np.concatenate([low[window_start:i], low[i + 1 : window_end]])
        if neighborhood_low.size > 0 and low[i] < neighborhood_low.min():
            lows.append(PivotPoint(index=i, price=float(low[i])))

    return PivotResult(highs=highs, lows=lows, formula=_PIVOT_FORMULA)
