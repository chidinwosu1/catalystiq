"""Real, deterministic technical indicators computed from price/volume
history (§5.3 Technical Trend, §5.4 Momentum, §5.5 Volume/Liquidity, §5.6
Volatility of the quantitative-scoring spec).

Everything here is a documented mathematical transform of real OHLCV
data - no machine learning, no invented numbers. This is deliberately
*not* the full spec: a genuine Confidence Score, calibrated Bullish/
Bearish probabilities, a Rating, or any of the five independent scores
require a trained, backtested, calibrated model, which this build does
not have (no real historical training pipeline or outbound market-data
access in this environment). When there isn't enough price history for a
given indicator, or for its historical percentile/z-score (§8, requires
at least three years of valid history), the reading is marked
"insufficient_data" instead of guessing.
"""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

from catalystiq.schemas.analysis import IndicatorReading, TechnicalSnapshot
from catalystiq.schemas.market_data import OHLCVBar

# §6.1/§8: historical percentile/z-score require at least three years of
# valid history. The field is still named `percentile_5y` (matching the
# existing `IndicatorSnapshot.percentile_5y` column) since five years is
# the spec's preferred depth - three years is only the enforced minimum.
_PERCENTILE_MIN_HISTORY_DAYS = 365 * 3


def _bars_to_frame(bars: list[OHLCVBar]) -> pd.DataFrame:
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


def _sma_series(closes: pd.Series, window: int) -> pd.Series:
    return closes.rolling(window=window, min_periods=window).mean()


def _price_vs_ma_series(closes: pd.Series, ma: pd.Series) -> pd.Series:
    return (closes - ma) / ma * 100


def _ma_slope_series(ma: pd.Series, lookback: int) -> pd.Series:
    prior = ma.shift(lookback)
    return (ma - prior) / prior * 100


def _rsi_series(closes: pd.Series, period: int = 14) -> pd.Series:
    delta = closes.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()

    with np.errstate(divide="ignore", invalid="ignore"):
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
    rsi = np.where(avg_loss == 0, np.where(avg_gain == 0, 50.0, 100.0), rsi)
    return pd.Series(rsi, index=closes.index)


def _macd_series(
    closes: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9
) -> tuple[pd.Series, pd.Series, pd.Series]:
    ema_fast = closes.ewm(span=fast, adjust=False, min_periods=fast).mean()
    ema_slow = closes.ewm(span=slow, adjust=False, min_periods=slow).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False, min_periods=signal).mean()
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def _bollinger_series(
    closes: pd.Series, window: int = 20, num_std: int = 2
) -> tuple[pd.Series, pd.Series]:
    mid = closes.rolling(window=window, min_periods=window).mean()
    std = closes.rolling(window=window, min_periods=window).std()
    upper = mid + num_std * std
    lower = mid - num_std * std
    percent_b = (closes - lower) / (upper - lower) * 100
    bandwidth_pct = (upper - lower) / mid * 100
    return percent_b, bandwidth_pct


def _atr_series(df: pd.DataFrame, period: int = 14) -> tuple[pd.Series, pd.Series]:
    prev_close = df["close"].shift(1)
    true_range = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr = true_range.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    atr_pct = atr / df["close"] * 100
    return atr, atr_pct


def _realized_volatility_series(closes: pd.Series, window: int = 20) -> pd.Series:
    log_returns = np.log(closes / closes.shift(1))
    return log_returns.rolling(window=window, min_periods=window).std() * (252**0.5) * 100


def _relative_volume_series(volume: pd.Series, window: int = 20) -> pd.Series:
    prior_avg = volume.shift(1).rolling(window=window, min_periods=window).mean()
    return volume / prior_avg * 100


def _obv_series(df: pd.DataFrame) -> pd.Series:
    direction = np.sign(df["close"].diff()).fillna(0)
    return (direction * df["volume"]).cumsum()


def _make_reading(
    name: str,
    series: pd.Series,
    description: str,
    params: dict[str, int],
    min_bars_required: int,
    bars_used: int,
    history_days_available: int,
) -> IndicatorReading:
    """Builds an IndicatorReading from a full historical series of a
    computed indicator's values (NaN where there wasn't enough warm-up
    data yet). The percentile/z-score are the current value's rank within
    that same indicator's own historical distribution (§8).
    """
    valid = series.dropna()
    if bars_used < min_bars_required or valid.empty:
        return IndicatorReading(
            name=name,
            status="insufficient_data",
            value=None,
            description=description,
            params=params,
            min_bars_required=min_bars_required,
        )

    value = float(valid.iloc[-1])
    percentile_5y: float | None = None
    zscore_5y: float | None = None
    if history_days_available >= _PERCENTILE_MIN_HISTORY_DAYS and len(valid) >= 2:
        percentile_5y = float((valid <= value).sum() / len(valid) * 100)
        std = float(valid.std())
        if std > 0:
            zscore_5y = float((value - valid.mean()) / std)

    return IndicatorReading(
        name=name,
        status="computed",
        value=value,
        description=description,
        params=params,
        min_bars_required=min_bars_required,
        percentile_5y=percentile_5y,
        zscore_5y=zscore_5y,
    )


def compute_technical_snapshot(symbol: str, bars: list[OHLCVBar]) -> TechnicalSnapshot:
    """Computes every indicator below from real OHLCV bars. Bars are sorted
    by date first; callers should already have run them through the Data
    Validation Layer (§2.9).
    """
    bars = sorted(bars, key=lambda b: b.date)
    bars_used = len(bars)

    if not bars:
        return TechnicalSnapshot(
            symbol=symbol.upper(),
            as_of=dt.datetime.now(dt.timezone.utc),
            bars_used=0,
            history_days_available=0,
            indicators=[],
            warnings=["No price history available."],
        )

    history_days_available = (bars[-1].date - bars[0].date).days
    df = _bars_to_frame(bars)
    closes = df["close"]

    def reading(
        name: str, series: pd.Series, description: str, params: dict[str, int], min_bars: int
    ) -> IndicatorReading:
        return _make_reading(
            name, series, description, params, min_bars, bars_used, history_days_available
        )

    indicators: list[IndicatorReading] = []

    sma20 = _sma_series(closes, 20)
    sma50 = _sma_series(closes, 50)
    sma100 = _sma_series(closes, 100)
    sma200 = _sma_series(closes, 200)
    indicators.append(
        reading("sma_20", sma20, "20-day simple moving average of the close.", {"window": 20}, 20)
    )
    indicators.append(
        reading("sma_50", sma50, "50-day simple moving average of the close.", {"window": 50}, 50)
    )
    indicators.append(
        reading(
            "sma_100", sma100, "100-day simple moving average of the close.", {"window": 100}, 100
        )
    )
    indicators.append(
        reading(
            "sma_200", sma200, "200-day simple moving average of the close.", {"window": 200}, 200
        )
    )
    indicators.append(
        reading(
            "price_vs_sma_50_pct",
            _price_vs_ma_series(closes, sma50),
            "Percent distance of the latest close from the 50-day SMA.",
            {"window": 50},
            50,
        )
    )
    indicators.append(
        reading(
            "sma_50_slope_10d_pct",
            _ma_slope_series(sma50, 10),
            "Percent change in the 50-day SMA over the last 10 bars.",
            {"window": 50, "lookback": 10},
            60,
        )
    )

    indicators.append(
        reading(
            "rsi_14",
            _rsi_series(closes, 14),
            "14-period relative strength index (Wilder smoothing).",
            {"period": 14},
            14,
        )
    )

    macd_line, macd_signal, macd_hist = _macd_series(closes, 12, 26, 9)
    indicators.append(
        reading(
            "macd_line",
            macd_line,
            "MACD line: 12-day EMA of the close minus the 26-day EMA.",
            {"fast": 12, "slow": 26},
            26,
        )
    )
    indicators.append(
        reading(
            "macd_signal",
            macd_signal,
            "9-day EMA of the MACD line.",
            {"fast": 12, "slow": 26, "signal": 9},
            35,
        )
    )
    indicators.append(
        reading(
            "macd_histogram",
            macd_hist,
            "MACD line minus its signal line.",
            {"fast": 12, "slow": 26, "signal": 9},
            35,
        )
    )

    percent_b, bandwidth_pct = _bollinger_series(closes, 20, 2)
    indicators.append(
        reading(
            "bollinger_percent_b",
            percent_b,
            "Position of the close within the 20-day, 2-std Bollinger Bands "
            "(0 = lower band, 100 = upper band).",
            {"window": 20, "num_std": 2},
            20,
        )
    )
    indicators.append(
        reading(
            "bollinger_bandwidth_pct",
            bandwidth_pct,
            "Bollinger Band width as a percent of the midline (volatility proxy).",
            {"window": 20, "num_std": 2},
            20,
        )
    )

    atr, atr_pct = _atr_series(df, 14)
    indicators.append(
        reading(
            "atr_14",
            atr,
            "14-period average true range (Wilder smoothing), in price units.",
            {"period": 14},
            14,
        )
    )
    indicators.append(
        reading(
            "atr_14_pct",
            atr_pct,
            "14-period ATR as a percent of the latest close.",
            {"period": 14},
            14,
        )
    )

    indicators.append(
        reading(
            "realized_volatility_20d_annualized_pct",
            _realized_volatility_series(closes, 20),
            "Annualized standard deviation of 20-day daily log returns.",
            {"window": 20},
            21,
        )
    )

    indicators.append(
        reading(
            "relative_volume_20d_pct",
            _relative_volume_series(df["volume"], 20),
            "Today's volume as a percent of the prior 20-day average volume.",
            {"window": 20},
            21,
        )
    )

    indicators.append(
        reading(
            "obv",
            _obv_series(df),
            "On-balance volume: cumulative volume added or subtracted by close direction.",
            {},
            2,
        )
    )

    warnings: list[str] = []
    if history_days_available < _PERCENTILE_MIN_HISTORY_DAYS:
        warnings.append(
            f"Only {history_days_available} days of history available; the 3-year minimum "
            "for a historical percentile/z-score isn't met, so those fields are omitted for "
            "every indicator."
        )

    return TechnicalSnapshot(
        symbol=symbol.upper(),
        as_of=dt.datetime.now(dt.timezone.utc),
        bars_used=bars_used,
        history_days_available=history_days_available,
        indicators=indicators,
        warnings=warnings,
    )
