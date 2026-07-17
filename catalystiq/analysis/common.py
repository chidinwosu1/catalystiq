"""Shared helpers reused by every analytical data product in this package.

Centralizes the OHLCV-to-DataFrame conversion and the historical percentile/
z-score calculation (§8: "at least three years of valid history") so every
product enforces the same rule as the technical indicator engine
(catalystiq/analysis/indicators.py): insufficient history returns null,
never a guessed number. New products should build their FeatureReading
values through `make_reading()` below for a consistent shape.
"""
from __future__ import annotations

import pandas as pd

from catalystiq.schemas.analysis import FeatureReading, FeatureStatus
from catalystiq.schemas.market_data import OHLCVBar

PERCENTILE_MIN_HISTORY_DAYS = 365 * 3


def bars_to_frame(bars: list[OHLCVBar]) -> pd.DataFrame:
    """Sorts by date and returns a DataFrame indexed by date - the shared
    input shape every product's calculations start from."""
    bars = sorted(bars, key=lambda b: b.date)
    return pd.DataFrame(
        {
            "open": [b.open for b in bars],
            "high": [b.high for b in bars],
            "low": [b.low for b in bars],
            "close": [b.close for b in bars],
            "volume": [b.volume for b in bars],
        },
        index=pd.DatetimeIndex([b.date for b in bars], name="date"),
    )


def history_days_available(bars: list[OHLCVBar]) -> int:
    if not bars:
        return 0
    bars = sorted(bars, key=lambda b: b.date)
    return (bars[-1].date - bars[0].date).days


def historical_percentile_zscore(
    series: pd.Series, days_available: int
) -> tuple[float | None, float | None]:
    """Percentile/z-score of `series`'s last valid value within its own
    historical distribution, or (None, None) if there isn't at least three
    years of history or fewer than 2 valid observations exist."""
    valid = series.dropna()
    if days_available < PERCENTILE_MIN_HISTORY_DAYS or len(valid) < 2:
        return None, None

    value = float(valid.iloc[-1])
    percentile = float((valid <= value).sum() / len(valid) * 100)
    std = float(valid.std())
    zscore = float((value - valid.mean()) / std) if std > 0 else None
    return percentile, zscore


def make_reading(
    name: str,
    value: int | float | str | bool | None,
    description: str,
    params: dict[str, int | float | str] | None = None,
    status: FeatureStatus = "available",
    percentile_5y: float | None = None,
    zscore_5y: float | None = None,
    calculation_version: str = "1.0.0",
) -> FeatureReading:
    return FeatureReading(
        name=name,
        status=status,
        value=value,
        description=description,
        params=params or {},
        calculation_version=calculation_version,
        percentile_5y=percentile_5y,
        zscore_5y=zscore_5y,
    )


def insufficient(
    name: str,
    description: str,
    params: dict[str, int | float | str] | None = None,
    calculation_version: str = "1.0.0",
) -> FeatureReading:
    """Convenience for the "not enough bars" case - value stays null."""
    return make_reading(
        name,
        None,
        description,
        params,
        status="insufficient_data",
        calculation_version=calculation_version,
    )
