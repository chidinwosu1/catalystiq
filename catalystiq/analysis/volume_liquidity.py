"""Volume & Liquidity data product (§8 of the quantitative-scoring spec).

Every metric is a documented formula over real OHLCV data. `turnover_ratio`
needs shares-outstanding (available from `FundamentalsSnapshot`, not fetched
by this product directly - callers may pass it in); bid/ask spread and
slippage-band metrics are `not_supported` since the current
`MarketDataProvider` has no quote-level bid/ask data source.
"""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

from catalystiq.analysis.common import bars_to_frame, history_days_available, insufficient, make_reading
from catalystiq.schemas.analysis import FeatureReading
from catalystiq.schemas.market_data import OHLCVBar
from catalystiq.schemas.volume_liquidity import VolumeLiquiditySnapshot

# --- Configuration (documented, to be promoted to versioned config per §25) ---
ADV_WINDOWS = (5, 20, 60, 200)
RELATIVE_VOLUME_WINDOW = 20
DOLLAR_VOLUME_MEDIAN_WINDOW = 20
VOLUME_ZSCORE_WINDOW = 20
UP_DOWN_VOLUME_WINDOW = 20
CMF_PERIOD = 20
MFI_PERIOD = 14
TREND_SLOPE_WINDOW = 10
DIVERGENCE_WINDOW = 20

LIQUIDITY_HIGH_THRESHOLD = 10_000_000.0
LIQUIDITY_MODERATE_THRESHOLD = 1_000_000.0
LIQUIDITY_LOW_THRESHOLD = 100_000.0


def _money_flow_multiplier(df: pd.DataFrame) -> pd.Series:
    rng = df["high"] - df["low"]
    mfm = ((df["close"] - df["low"]) - (df["high"] - df["close"])) / rng
    return mfm.replace([np.inf, -np.inf], 0.0).fillna(0.0)


def _adl_series(df: pd.DataFrame) -> pd.Series:
    return (_money_flow_multiplier(df) * df["volume"]).cumsum()


def _cmf_series(df: pd.DataFrame, period: int) -> pd.Series:
    mf_volume = _money_flow_multiplier(df) * df["volume"]
    return mf_volume.rolling(period, min_periods=period).sum() / df["volume"].rolling(period, min_periods=period).sum()


def _mfi_series(df: pd.DataFrame, period: int) -> pd.Series:
    typical_price = (df["high"] + df["low"] + df["close"]) / 3
    raw_money_flow = typical_price * df["volume"]
    direction = typical_price.diff()
    positive_flow = raw_money_flow.where(direction > 0, 0.0)
    negative_flow = raw_money_flow.where(direction < 0, 0.0)
    pos_sum = positive_flow.rolling(period, min_periods=period).sum()
    neg_sum = negative_flow.rolling(period, min_periods=period).sum()

    with np.errstate(divide="ignore", invalid="ignore"):
        money_ratio = pos_sum / neg_sum
        mfi = 100 - (100 / (1 + money_ratio))
    mfi = np.where(neg_sum == 0, np.where(pos_sum == 0, 50.0, 100.0), mfi)
    return pd.Series(mfi, index=df.index)


def _vpt_series(df: pd.DataFrame) -> pd.Series:
    pct_change = df["close"].pct_change()
    return (df["volume"] * pct_change).cumsum()


def _obv_series(df: pd.DataFrame) -> pd.Series:
    direction = np.sign(df["close"].diff()).fillna(0)
    return (direction * df["volume"]).cumsum()


def _slope_label(series: pd.Series, window: int) -> str | None:
    if len(series) < window + 1 or series.iloc[-1 - window] == 0:
        return None
    change = (series.iloc[-1] - series.iloc[-1 - window]) / abs(series.iloc[-1 - window])
    if change > 0.02:
        return "rising"
    if change < -0.02:
        return "falling"
    return "flat"


def _liquidity_classification(median_dollar_volume: float | None) -> FeatureReading:
    description = "Rule-based liquidity classification from rolling median dollar volume."
    if median_dollar_volume is None:
        return make_reading("liquidity_classification", "unknown", description, {}, status="insufficient_data")

    if median_dollar_volume >= LIQUIDITY_HIGH_THRESHOLD:
        label = "high"
    elif median_dollar_volume >= LIQUIDITY_MODERATE_THRESHOLD:
        label = "moderate"
    elif median_dollar_volume >= LIQUIDITY_LOW_THRESHOLD:
        label = "low"
    else:
        label = "very_low"

    return make_reading(
        "liquidity_classification",
        label,
        description,
        {"median_dollar_volume": round(median_dollar_volume, 2)},
    )


def compute_volume_liquidity_snapshot(
    symbol: str,
    bars: list[OHLCVBar],
    shares_outstanding: float | None = None,
) -> VolumeLiquiditySnapshot:
    bars_used = len(bars)
    days_available = history_days_available(bars)
    now = dt.datetime.now(dt.timezone.utc)

    if bars_used < 2:
        return VolumeLiquiditySnapshot(
            symbol=symbol.upper(),
            as_of=now,
            bars_used=bars_used,
            history_days_available=days_available,
            metrics=[],
            liquidity_classification=_liquidity_classification(None),
            warnings=["Not enough bars to compute volume/liquidity metrics."],
        )

    df = bars_to_frame(bars)
    close, volume = df["close"], df["volume"]
    dollar_volume = close * volume

    metrics: list[FeatureReading] = []

    for window in ADV_WINDOWS:
        name = f"average_daily_volume_{window}d"
        if bars_used < window:
            metrics.append(insufficient(name, f"Average share volume over the last {window} sessions.", {"window": window}))
            continue
        metrics.append(make_reading(name, round(float(volume.tail(window).mean()), 2), f"Average share volume over the last {window} sessions.", {"window": window}))

    if bars_used > RELATIVE_VOLUME_WINDOW:
        prior_avg = float(volume.iloc[-1 - RELATIVE_VOLUME_WINDOW:-1].mean())
        relative_volume = float(volume.iloc[-1] / prior_avg * 100) if prior_avg > 0 else None
        metrics.append(make_reading("relative_volume_pct", round(relative_volume, 2) if relative_volume is not None else None, f"Today's volume as percent of the prior {RELATIVE_VOLUME_WINDOW}-day average.", {"window": RELATIVE_VOLUME_WINDOW}, status="available" if relative_volume is not None else "invalid"))
    else:
        metrics.append(insufficient("relative_volume_pct", f"Today's volume as percent of the prior {RELATIVE_VOLUME_WINDOW}-day average.", {"window": RELATIVE_VOLUME_WINDOW}))

    metrics.append(make_reading("dollar_volume", round(float(dollar_volume.iloc[-1]), 2), "Latest session's close price times volume.", {}))

    median_dv = None
    if bars_used >= DOLLAR_VOLUME_MEDIAN_WINDOW:
        median_dv = float(dollar_volume.tail(DOLLAR_VOLUME_MEDIAN_WINDOW).median())
        metrics.append(make_reading("rolling_median_dollar_volume", round(median_dv, 2), f"Median dollar volume over the last {DOLLAR_VOLUME_MEDIAN_WINDOW} sessions.", {"window": DOLLAR_VOLUME_MEDIAN_WINDOW}))
    else:
        metrics.append(insufficient("rolling_median_dollar_volume", f"Median dollar volume over the last {DOLLAR_VOLUME_MEDIAN_WINDOW} sessions.", {"window": DOLLAR_VOLUME_MEDIAN_WINDOW}))

    if bars_used >= VOLUME_ZSCORE_WINDOW:
        window_vol = volume.tail(VOLUME_ZSCORE_WINDOW)
        std = float(window_vol.std())
        zscore = float((volume.iloc[-1] - window_vol.mean()) / std) if std > 0 else 0.0
        metrics.append(make_reading("volume_zscore", round(zscore, 4), f"Z-score of today's volume vs. the last {VOLUME_ZSCORE_WINDOW} sessions.", {"window": VOLUME_ZSCORE_WINDOW}))
    else:
        metrics.append(insufficient("volume_zscore", f"Z-score of today's volume vs. the last {VOLUME_ZSCORE_WINDOW} sessions.", {"window": VOLUME_ZSCORE_WINDOW}))

    if bars_used > UP_DOWN_VOLUME_WINDOW:
        recent = df.tail(UP_DOWN_VOLUME_WINDOW + 1)
        price_change = recent["close"].diff().iloc[1:]
        vol_slice = recent["volume"].iloc[1:]
        up_volume = float(vol_slice[price_change > 0].sum())
        down_volume = float(vol_slice[price_change < 0].sum())
        metrics.append(make_reading("up_volume", up_volume, f"Total volume on up sessions over the last {UP_DOWN_VOLUME_WINDOW} sessions.", {"window": UP_DOWN_VOLUME_WINDOW}))
        metrics.append(make_reading("down_volume", down_volume, f"Total volume on down sessions over the last {UP_DOWN_VOLUME_WINDOW} sessions.", {"window": UP_DOWN_VOLUME_WINDOW}))
    else:
        metrics.append(insufficient("up_volume", f"Total volume on up sessions over the last {UP_DOWN_VOLUME_WINDOW} sessions.", {"window": UP_DOWN_VOLUME_WINDOW}))
        metrics.append(insufficient("down_volume", f"Total volume on down sessions over the last {UP_DOWN_VOLUME_WINDOW} sessions.", {"window": UP_DOWN_VOLUME_WINDOW}))

    adl = _adl_series(df)
    metrics.append(make_reading("accumulation_distribution_line", round(float(adl.iloc[-1]), 2), "Accumulation/Distribution line, cumulative.", {}))

    if bars_used >= CMF_PERIOD:
        cmf = _cmf_series(df, CMF_PERIOD)
        metrics.append(make_reading("chaikin_money_flow", round(float(cmf.iloc[-1]), 4), f"Chaikin Money Flow over {CMF_PERIOD} sessions.", {"period": CMF_PERIOD}))
    else:
        metrics.append(insufficient("chaikin_money_flow", f"Chaikin Money Flow over {CMF_PERIOD} sessions.", {"period": CMF_PERIOD}))

    if bars_used >= MFI_PERIOD + 1:
        mfi = _mfi_series(df, MFI_PERIOD)
        metrics.append(make_reading("money_flow_index", round(float(mfi.iloc[-1]), 4), f"Money Flow Index, {MFI_PERIOD}-period.", {"period": MFI_PERIOD}))
    else:
        metrics.append(insufficient("money_flow_index", f"Money Flow Index, {MFI_PERIOD}-period.", {"period": MFI_PERIOD}))

    vpt = _vpt_series(df)
    metrics.append(make_reading("volume_price_trend", round(float(vpt.fillna(0).iloc[-1]), 4), "Volume Price Trend, cumulative.", {}))

    obv = _obv_series(df)
    obv_slope = _slope_label(obv, TREND_SLOPE_WINDOW)
    metrics.append(make_reading("obv_slope", obv_slope, f"Direction of OBV over the last {TREND_SLOPE_WINDOW} bars.", {"window": TREND_SLOPE_WINDOW}, status="available" if obv_slope else "insufficient_data"))

    if bars_used > TREND_SLOPE_WINDOW:
        adv20 = volume.rolling(20, min_periods=1).mean()
        volume_trend = _slope_label(adv20, TREND_SLOPE_WINDOW)
        metrics.append(make_reading("volume_trend", volume_trend, f"Direction of 20-day average volume over the last {TREND_SLOPE_WINDOW} bars.", {"window": TREND_SLOPE_WINDOW}, status="available" if volume_trend else "insufficient_data"))
    else:
        metrics.append(insufficient("volume_trend", f"Direction of 20-day average volume over the last {TREND_SLOPE_WINDOW} bars.", {"window": TREND_SLOPE_WINDOW}))

    if bars_used > DIVERGENCE_WINDOW:
        price_change_pct = float((close.iloc[-1] - close.iloc[-1 - DIVERGENCE_WINDOW]) / close.iloc[-1 - DIVERGENCE_WINDOW] * 100)
        volume_confirms = (
            price_change_pct > 0 and (obv.iloc[-1] > obv.iloc[-1 - DIVERGENCE_WINDOW])
        ) or (
            price_change_pct < 0 and (obv.iloc[-1] < obv.iloc[-1 - DIVERGENCE_WINDOW])
        )
        if abs(price_change_pct) < 0.5:
            confirmation_label = "neutral"
        elif volume_confirms:
            confirmation_label = "confirmed_up" if price_change_pct > 0 else "confirmed_down"
        else:
            confirmation_label = "divergent_up_weak_volume" if price_change_pct > 0 else "divergent_down_weak_volume"

        metrics.append(
            make_reading(
                "volume_confirmation_of_price",
                confirmation_label,
                f"Whether OBV direction confirms the {DIVERGENCE_WINDOW}-session price move.",
                {"window": DIVERGENCE_WINDOW},
            )
        )
        metrics.append(
            make_reading(
                "volume_divergence_from_price",
                bool(not volume_confirms and abs(price_change_pct) >= 0.5),
                f"Whether OBV direction disagrees with the {DIVERGENCE_WINDOW}-session price move.",
                {"window": DIVERGENCE_WINDOW},
            )
        )
    else:
        metrics.append(insufficient("volume_confirmation_of_price", f"Whether OBV direction confirms the {DIVERGENCE_WINDOW}-session price move.", {"window": DIVERGENCE_WINDOW}))
        metrics.append(insufficient("volume_divergence_from_price", f"Whether OBV direction disagrees with the {DIVERGENCE_WINDOW}-session price move.", {"window": DIVERGENCE_WINDOW}))

    if shares_outstanding:
        turnover = float(volume.iloc[-1] / shares_outstanding * 100)
        metrics.append(make_reading("turnover_ratio_pct", round(turnover, 4), "Latest session's volume as percent of shares outstanding.", {"shares_outstanding": shares_outstanding}))
    else:
        metrics.append(make_reading("turnover_ratio_pct", None, "Latest session's volume as percent of shares outstanding.", {}, status="not_supported"))

    metrics.append(make_reading("bid_ask_spread", None, "Bid/ask spread.", {}, status="not_supported"))
    metrics.append(make_reading("spread_pct_of_mid", None, "Bid/ask spread as percent of mid-price.", {}, status="not_supported"))
    metrics.append(make_reading("estimated_slippage_band", None, "Estimated slippage band from market depth.", {}, status="not_supported"))

    return VolumeLiquiditySnapshot(
        symbol=symbol.upper(),
        as_of=now,
        bars_used=bars_used,
        history_days_available=days_available,
        metrics=metrics,
        liquidity_classification=_liquidity_classification(median_dv),
        warnings=[],
    )
