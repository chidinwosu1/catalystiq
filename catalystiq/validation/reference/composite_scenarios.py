"""Synthetic OHLCV scenarios with a known-by-construction expected outcome
for Catalyst IQ's composite, decision-rule outputs - market regime, trend
structure, breakout state, and liquidity classification.

These genuinely have no single universal external reference value (no
TA-Lib function or TradingView built-in computes "is this a strong
uptrend," only the primitives regime.py's rules combine) - see
catalystiq/analysis/market_structure.py and volume_liquidity.py's own
module docstrings for the exact decision rules each scenario exercises.
Validated here via construction (a series built to unambiguously satisfy
one rule branch and no other) and consumed by
tests/test_composite_reference_scenarios.py, rather than via numeric
tolerance comparison against any external source.
"""
from __future__ import annotations

import datetime as dt

from catalystiq.schemas.market_data import OHLCVBar


def _business_days(start: dt.date, n: int) -> list[dt.date]:
    days: list[dt.date] = []
    d = start
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d)
        d += dt.timedelta(days=1)
    return days


def _bars_from_closes(dates: list[dt.date], closes: list[float], volume: int = 1_000_000) -> list[OHLCVBar]:
    bars = []
    for date, close in zip(dates, closes):
        bars.append(
            OHLCVBar(
                date=date,
                open=close * 0.999,
                high=close * 1.006,
                low=close * 0.994,
                close=close,
                volume=volume,
            )
        )
    return bars


def strong_uptrend_scenario(n: int = 260) -> list[OHLCVBar]:
    """Steady, low-noise appreciation - price stays above SMA20>SMA50>SMA200
    with a strong ADX. Expected: regime == "strong_uptrend"."""
    dates = _business_days(dt.date(2020, 1, 2), n)
    closes = [100.0 * (1.0025**i) for i in range(n)]
    return _bars_from_closes(dates, closes)


def strong_downtrend_scenario(n: int = 260) -> list[OHLCVBar]:
    """Mirror of strong_uptrend_scenario. Expected: regime ==
    "strong_downtrend"."""
    dates = _business_days(dt.date(2020, 1, 2), n)
    closes = [100.0 * (0.9975**i) for i in range(n)]
    return _bars_from_closes(dates, closes)


def sideways_low_volatility_scenario(n: int = 260) -> list[OHLCVBar]:
    """A choppy, no-net-direction series whose amplitude tapers down over
    the back half - so ATR at the measurement point (the last bar) sits
    in the bottom half of its own trailing 60-day history, and price has
    no SMA20/50/200 directional alignment. Expected: regime ==
    "sideways_low_volatility"."""
    dates = _business_days(dt.date(2020, 1, 2), n)
    closes = []
    price = 100.0
    for i in range(n):
        # Amplitude ramps from 1.2 down to 0.1 over the series - high
        # enough early on to avoid a false volatility_contraction/
        # expansion delta trip, low enough at the tail to land ATR in the
        # bottom half of its own recent history.
        amplitude = max(0.05, 1.5 - i * (1.45 / n))
        step = amplitude if i % 2 == 0 else -amplitude
        price += step
        closes.append(price)
    return _bars_from_closes(dates, closes)


def higher_highs_higher_lows_scenario(n: int = 120) -> list[OHLCVBar]:
    """A rising zigzag: each swing high/low is higher than the one before.
    Expected: trend_structure == "higher_highs_higher_lows"."""
    import math

    dates = _business_days(dt.date(2020, 1, 2), n)
    closes = [100.0 + i * 0.4 + 6.0 * math.sin(i / 8.0) for i in range(n)]
    return _bars_from_closes(dates, closes)


def lower_highs_lower_lows_scenario(n: int = 120) -> list[OHLCVBar]:
    """Mirror of higher_highs_higher_lows_scenario. Expected:
    trend_structure == "lower_highs_lower_lows"."""
    import math

    dates = _business_days(dt.date(2020, 1, 2), n)
    closes = [150.0 - i * 0.4 + 6.0 * math.sin(i / 8.0) for i in range(n)]
    return _bars_from_closes(dates, closes)


def range_bound_scenario(n: int = 120) -> list[OHLCVBar]:
    """A zigzag with essentially flat swing highs and flat swing lows -
    the last two swing highs/lows differ by less than
    RANGE_BOUND_SWING_CHANGE_PCT. Expected: trend_structure ==
    "range_bound"."""
    import math

    dates = _business_days(dt.date(2020, 1, 2), n)
    closes = [100.0 + 5.0 * math.sin(i / 8.0) for i in range(n)]
    return _bars_from_closes(dates, closes)


def failed_breakout_scenario(n: int = 80) -> list[OHLCVBar]:
    """A flat consolidation range, a decisive move above the range high on
    above-average volume, then a pullback that closes back below the most
    recent high without confirming. Expected: breakout_state ==
    "failed_breakout"."""
    import math

    dates = _business_days(dt.date(2020, 1, 2), n)
    closes = []
    volumes = []
    for i in range(n - 5):
        closes.append(100.0 + 1.5 * math.sin(i / 6.0))
        volumes.append(1_000_000)
    range_high = max(closes)
    breakout_closes = [range_high * m for m in (1.30, 1.38, 1.42, 1.37, 1.34)]
    for close, volume in zip(breakout_closes, (3_000_000, 3_500_000, 3_200_000, 2_200_000, 2_000_000)):
        closes.append(close)
        volumes.append(volume)
    bars = []
    for date, close, volume in zip(dates, closes, volumes):
        bars.append(
            OHLCVBar(date=date, open=close * 0.999, high=close * 1.006, low=close * 0.994, close=close, volume=volume)
        )
    return bars


def high_liquidity_scenario(n: int = 30) -> list[OHLCVBar]:
    """Large, consistent dollar volume (price * volume well above the
    high-liquidity threshold). Expected: liquidity_classification ==
    "high"."""
    dates = _business_days(dt.date(2024, 1, 2), n)
    closes = [100.0 + i * 0.1 for i in range(n)]
    return _bars_from_closes(dates, closes, volume=500_000)  # 500k * ~$100 = $50M/day


def very_low_liquidity_scenario(n: int = 30) -> list[OHLCVBar]:
    """Tiny, consistent dollar volume, well under the low-liquidity
    threshold. Expected: liquidity_classification == "very_low"."""
    dates = _business_days(dt.date(2024, 1, 2), n)
    closes = [5.0 + i * 0.01 for i in range(n)]
    return _bars_from_closes(dates, closes, volume=1_000)  # 1k * ~$5 = $5k/day
