"""Market Structure data product (§6 of the quantitative-scoring spec).

Translates price behavior into structural observations - swing points,
trend structure, support/resistance, breakout/breakdown state, gaps, and a
rule-based regime classification - without making unsupported predictions.
Every threshold below is a named module constant (documented as
config-driven per §25; not yet wired into catalystiq/config.py's versioned
settings - see the README note added alongside this module for that
follow-up).

Deliberately does not import catalystiq/analysis/indicators.py's private
(underscore-prefixed) helpers, to keep this product independently
buildable/testable per §3.1 ("no analytical data product may directly
depend on" another module's internals) - small SMA/ATR helpers are
reimplemented locally instead.
"""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

from catalystiq.analysis.common import bars_to_frame, history_days_available, make_reading
from catalystiq.schemas.analysis import FeatureReading
from catalystiq.schemas.market_data import OHLCVBar
from catalystiq.schemas.market_structure import (
    MarketStructureSnapshot,
    SupportResistanceLevel,
    SwingPoint,
)

# --- Configuration (documented, to be promoted to versioned config per §25) ---
SWING_LEFT_BARS = 5
SWING_RIGHT_BARS = 5
SWING_MAX_STRENGTH_CHECK = 20
SWING_POINTS_RETURNED = 10

LEVEL_CLUSTER_TOLERANCE_PCT = 1.0  # swing points within this % are the same level
LEVEL_BROKEN_BUFFER_PCT = 0.5  # close beyond the level by this much marks it "broken"

BREAKOUT_MIN_PENETRATION_PCT = 0.5
BREAKOUT_APPROACH_PCT = 2.0
BREAKOUT_CONFIRM_RELATIVE_VOLUME = 1.2
BREAKOUT_LOOKBACK_BARS = 10  # window to detect a recent breakout for retest/failed states

RANGE_BOUND_SWING_CHANGE_PCT = 1.5  # last-two-highs / last-two-lows pct change threshold

ADX_PERIOD = 14
ADX_STRONG_TREND = 25
ATR_PERIOD = 14
ATR_VOL_WINDOW = 60  # lookback for the local (non-3y-gated) ATR-percentile used by regime
VOL_EXPANSION_DELTA = 15  # percentile-point jump over VOL_EXPANSION_LOOKBACK_BARS
VOL_EXPANSION_LOOKBACK_BARS = 10
SIDEWAYS_VOL_SPLIT_PERCENTILE = 50

_CALC_VERSION = "1.0.0"


def _sma(closes: pd.Series, window: int) -> pd.Series:
    return closes.rolling(window=window, min_periods=window).mean()


def _true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["close"].shift(1)
    return pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)


def _atr_pct_series(df: pd.DataFrame, period: int = ATR_PERIOD) -> pd.Series:
    atr = _true_range(df).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    return atr / df["close"] * 100


def _adx_series(df: pd.DataFrame, period: int = ADX_PERIOD) -> pd.Series:
    """Wilder's ADX/+DI/-DI. Returns ADX only (the +DI/-DI aren't surfaced
    separately in this product's output, only used internally for regime)."""
    up_move = df["high"].diff()
    down_move = -df["low"].diff()
    plus_dm = pd.Series(np.where((up_move > down_move) & (up_move > 0), up_move, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((down_move > up_move) & (down_move > 0), down_move, 0.0), index=df.index)

    tr = _true_range(df)
    atr = tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    plus_di = 100 * plus_dm.ewm(alpha=1 / period, adjust=False, min_periods=period).mean() / atr
    minus_di = 100 * minus_dm.ewm(alpha=1 / period, adjust=False, min_periods=period).mean() / atr

    with np.errstate(divide="ignore", invalid="ignore"):
        dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
    dx = dx.replace([np.inf, -np.inf], np.nan)
    return dx.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def _rolling_percentile(series: pd.Series, window: int) -> pd.Series:
    """Percentile of each value within its own trailing `window`-bar history.
    Unlike common.historical_percentile_zscore, this is NOT gated at three
    years of history - regime classification needs "high or low for this
    stock recently," not a rigorous multi-year statistic."""
    return series.rolling(window=window, min_periods=min(window, 20)).apply(
        lambda w: (w <= w.iloc[-1]).sum() / len(w) * 100, raw=False
    )


def _pivot_strength(values: pd.Series, idx: int, kind: str, max_check: int) -> int:
    """How many bars on each side (up to max_check) this pivot remains the
    strict extreme - a simple, deterministic "how dominant is this pivot"
    measure, capped at max_check."""
    n = len(values)
    strength = 0
    for k in range(1, max_check + 1):
        if idx - k < 0 or idx + k >= n:
            break
        if kind == "high":
            ok = values.iloc[idx] > values.iloc[idx - k] and values.iloc[idx] > values.iloc[idx + k]
        else:
            ok = values.iloc[idx] < values.iloc[idx - k] and values.iloc[idx] < values.iloc[idx + k]
        if not ok:
            break
        strength = k
    return strength


def _swing_points(
    df: pd.DataFrame, left: int = SWING_LEFT_BARS, right: int = SWING_RIGHT_BARS
) -> tuple[list[SwingPoint], list[SwingPoint]]:
    """Fractal swing-point detection: bar i is a swing high if high[i] is
    strictly greater than every bar within `left` bars before and `right`
    bars after it (mirror for swing lows). A point is "confirmed" once
    enough future bars exist to have validated it (§6.1)."""
    n = len(df)
    highs, lows = df["high"], df["low"]
    dates = df.index
    swing_highs: list[SwingPoint] = []
    swing_lows: list[SwingPoint] = []

    for i in range(left, n):
        window_start = max(0, i - left)
        confirmed = i + right < n
        check_end = min(n, i + right + 1)

        # Strictly greater/less than every other bar in the available window.
        local_window_high = highs.iloc[window_start:check_end].drop(highs.index[i])
        is_high = bool(len(local_window_high) > 0 and highs.iloc[i] > local_window_high.max())

        local_window_low = lows.iloc[window_start:check_end].drop(lows.index[i])
        is_low = bool(len(local_window_low) > 0 and lows.iloc[i] < local_window_low.min())

        bars_since = n - 1 - i
        if is_high:
            swing_highs.append(
                SwingPoint(
                    kind="high",
                    date=dates[i].date(),
                    price=float(highs.iloc[i]),
                    pivot_strength=_pivot_strength(highs, i, "high", SWING_MAX_STRENGTH_CHECK),
                    confirmed=confirmed,
                    bars_since=bars_since,
                )
            )
        if is_low:
            swing_lows.append(
                SwingPoint(
                    kind="low",
                    date=dates[i].date(),
                    price=float(lows.iloc[i]),
                    pivot_strength=_pivot_strength(lows, i, "low", SWING_MAX_STRENGTH_CHECK),
                    confirmed=confirmed,
                    bars_since=bars_since,
                )
            )

    return swing_highs[-SWING_POINTS_RETURNED:], swing_lows[-SWING_POINTS_RETURNED:]


def _consecutive_run(values: list[float], increasing: bool) -> int:
    """Counts, walking backward from the most recent value, how many
    consecutive comparisons satisfy strictly-increasing (or -decreasing)."""
    if len(values) < 2:
        return 0
    count = 0
    for i in range(len(values) - 1, 0, -1):
        if (increasing and values[i] > values[i - 1]) or (not increasing and values[i] < values[i - 1]):
            count += 1
        else:
            break
    return count


def _trend_structure(
    swing_highs: list[SwingPoint], swing_lows: list[SwingPoint], bars_used: int
) -> tuple[FeatureReading, FeatureReading, FeatureReading, FeatureReading, FeatureReading, FeatureReading]:
    confirmed_highs = [s for s in swing_highs if s.confirmed]
    confirmed_lows = [s for s in swing_lows if s.confirmed]

    if len(confirmed_highs) < 2 or len(confirmed_lows) < 2:
        insufficient = make_reading(
            "trend_structure", None, "Classifies recent swing structure.", {}, status="insufficient_data"
        )
        zero = make_reading("consecutive_count", 0, "Consecutive run count.", {}, status="insufficient_data")
        return insufficient, zero, zero, zero, zero, zero

    high_prices = [s.price for s in confirmed_highs]
    low_prices = [s.price for s in confirmed_lows]

    last_two_highs_pct = (high_prices[-1] - high_prices[-2]) / high_prices[-2] * 100
    last_two_lows_pct = (low_prices[-1] - low_prices[-2]) / low_prices[-2] * 100

    higher_high = high_prices[-1] > high_prices[-2]
    higher_low = low_prices[-1] > low_prices[-2]
    lower_high = high_prices[-1] < high_prices[-2]
    lower_low = low_prices[-1] < low_prices[-2]

    if abs(last_two_highs_pct) < RANGE_BOUND_SWING_CHANGE_PCT and abs(last_two_lows_pct) < RANGE_BOUND_SWING_CHANGE_PCT:
        label = "range_bound"
    elif higher_high and higher_low:
        label = "higher_highs_higher_lows"
    elif lower_high and lower_low:
        label = "lower_highs_lower_lows"
    else:
        label = "mixed_structure"

    consecutive_hh = _consecutive_run(high_prices, increasing=True)
    consecutive_hl = _consecutive_run(low_prices, increasing=True)
    consecutive_lh = _consecutive_run(high_prices, increasing=False)
    consecutive_ll = _consecutive_run(low_prices, increasing=False)

    bars_since_change = min(confirmed_highs[-1].bars_since, confirmed_lows[-1].bars_since)

    return (
        make_reading("trend_structure", label, "Classification of recent swing-point structure.", {}),
        make_reading("consecutive_higher_highs", consecutive_hh, "Consecutive confirmed higher swing highs.", {}),
        make_reading("consecutive_higher_lows", consecutive_hl, "Consecutive confirmed higher swing lows.", {}),
        make_reading("consecutive_lower_highs", consecutive_lh, "Consecutive confirmed lower swing highs.", {}),
        make_reading("consecutive_lower_lows", consecutive_ll, "Consecutive confirmed lower swing lows.", {}),
        make_reading(
            "bars_since_structural_change",
            bars_since_change,
            "Bars since the most recent confirmed swing point (proxy for structural-change recency).",
            {},
        ),
    )


def _cluster_levels(points: list[SwingPoint], kind: str, tolerance_pct: float) -> list[dict]:
    """Groups nearby swing points into candidate levels, sorted by price."""
    sorted_points = sorted(points, key=lambda p: p.price)
    clusters: list[list[SwingPoint]] = []
    for point in sorted_points:
        if clusters and abs(point.price - clusters[-1][-1].price) / clusters[-1][-1].price * 100 <= tolerance_pct:
            clusters[-1].append(point)
        else:
            clusters.append([point])

    results = []
    for cluster in clusters:
        prices = [p.price for p in cluster]
        avg_pivot_strength = sum(p.pivot_strength for p in cluster) / len(cluster)
        results.append(
            {
                "price": sum(prices) / len(prices),
                "method": f"repeated_swing_{kind}",
                "touch_count": len(cluster),
                "first_observed_at": min(p.date for p in cluster),
                "last_tested_at": max(p.date for p in cluster),
                "avg_pivot_strength": avg_pivot_strength,
            }
        )
    return results


def _support_resistance_levels(
    df: pd.DataFrame, swing_highs: list[SwingPoint], swing_lows: list[SwingPoint], current_price: float
) -> list[SupportResistanceLevel]:
    levels: list[SupportResistanceLevel] = []

    for cluster in _cluster_levels(swing_lows, "low", LEVEL_CLUSTER_TOLERANCE_PCT):
        levels.append(_build_level(cluster, "support", current_price))
    for cluster in _cluster_levels(swing_highs, "high", LEVEL_CLUSTER_TOLERANCE_PCT):
        levels.append(_build_level(cluster, "resistance", current_price))

    # Prior single-session/week/month high-low, each a one-touch candidate level.
    if len(df) >= 2:
        prior_session = df.iloc[-2]
        levels.append(_single_level(prior_session["low"], "support", "prior_session_low", df.index[-2].date(), current_price))
        levels.append(_single_level(prior_session["high"], "resistance", "prior_session_high", df.index[-2].date(), current_price))

    for freq, label in (("W", "week"), ("ME", "month")):
        resampled = df["close"].resample(freq).agg(["min", "max"])
        resampled_low = df["low"].resample(freq).min()
        resampled_high = df["high"].resample(freq).max()
        if len(resampled_low) >= 2:
            prior_low = resampled_low.iloc[-2]
            prior_high = resampled_high.iloc[-2]
            prior_period_date = resampled_low.index[-2].date()
            levels.append(_single_level(prior_low, "support", f"prior_{label}_low", prior_period_date, current_price))
            levels.append(_single_level(prior_high, "resistance", f"prior_{label}_high", prior_period_date, current_price))

    return levels


def _build_level(cluster: dict, level_type: str, current_price: float) -> SupportResistanceLevel:
    price = cluster["price"]
    broken = (
        (level_type == "support" and current_price < price * (1 - LEVEL_BROKEN_BUFFER_PCT / 100))
        or (level_type == "resistance" and current_price > price * (1 + LEVEL_BROKEN_BUFFER_PCT / 100))
    )
    strength = min(100, round(15 * cluster["touch_count"] + 10 * cluster["avg_pivot_strength"]))
    return SupportResistanceLevel(
        price=round(price, 4),
        type=level_type,
        method=cluster["method"],
        touch_count=cluster["touch_count"],
        first_observed_at=cluster["first_observed_at"],
        last_tested_at=cluster["last_tested_at"],
        distance_from_price_pct=round((price - current_price) / current_price * 100, 4),
        status="broken" if broken else "active",
        strength_score=strength,
    )


def _single_level(
    price: float, level_type: str, method: str, observed_at: dt.date, current_price: float
) -> SupportResistanceLevel:
    broken = (
        (level_type == "support" and current_price < price * (1 - LEVEL_BROKEN_BUFFER_PCT / 100))
        or (level_type == "resistance" and current_price > price * (1 + LEVEL_BROKEN_BUFFER_PCT / 100))
    )
    return SupportResistanceLevel(
        price=round(float(price), 4),
        type=level_type,
        method=method,
        touch_count=1,
        first_observed_at=observed_at,
        last_tested_at=observed_at,
        distance_from_price_pct=round((price - current_price) / current_price * 100, 4),
        status="broken" if broken else "active",
        strength_score=15,
    )


def _breakout_state(
    df: pd.DataFrame, levels: list[SupportResistanceLevel], current_price: float
) -> FeatureReading:
    description = "Detected breakout/breakdown state relative to the nearest active support/resistance level."
    active = [lv for lv in levels if lv.status == "active"]
    resistances = sorted([lv for lv in active if lv.type == "resistance"], key=lambda lv: lv.price)
    supports = sorted([lv for lv in active if lv.type == "support"], key=lambda lv: lv.price, reverse=True)
    nearest_resistance = next((lv for lv in resistances if lv.price >= current_price), None)
    nearest_support = next((lv for lv in supports if lv.price <= current_price), None)

    if len(df) < 2 or (nearest_resistance is None and nearest_support is None):
        return make_reading("breakout_state", "no_significant_level_nearby", description, {})

    recent = df.tail(BREAKOUT_LOOKBACK_BARS)
    relative_volume = float(df["volume"].iloc[-1] / df["volume"].tail(20).mean()) if len(df) >= 20 else None

    if nearest_resistance is not None:
        pct_to_resistance = (nearest_resistance.price - current_price) / current_price * 100
        confirmed_breakout = (
            current_price > nearest_resistance.price * (1 + BREAKOUT_MIN_PENETRATION_PCT / 100)
            and (relative_volume is None or relative_volume >= BREAKOUT_CONFIRM_RELATIVE_VOLUME)
        )
        if confirmed_breakout:
            broke_out_before_today = bool((recent["close"].iloc[:-1] > nearest_resistance.price).any()) if len(recent) > 1 else False
            touched_back_near_level = bool(
                (recent["low"] <= nearest_resistance.price * (1 + BREAKOUT_APPROACH_PCT / 100)).any()
            )
            state = (
                "retest_after_breakout"
                if broke_out_before_today and touched_back_near_level
                else "confirmed_breakout"
            )
            return make_reading("breakout_state", state, description, {"level": round(nearest_resistance.price, 4)})
        if 0 < pct_to_resistance <= BREAKOUT_APPROACH_PCT:
            return make_reading("breakout_state", "approaching_resistance", description, {"level": round(nearest_resistance.price, 4)})
        if float(recent["high"].max()) > nearest_resistance.price and current_price < nearest_resistance.price:
            return make_reading("breakout_state", "failed_breakout", description, {"level": round(nearest_resistance.price, 4)})

    if nearest_support is not None:
        pct_to_support = (current_price - nearest_support.price) / current_price * 100
        confirmed_breakdown = (
            current_price < nearest_support.price * (1 - BREAKOUT_MIN_PENETRATION_PCT / 100)
            and (relative_volume is None or relative_volume >= BREAKOUT_CONFIRM_RELATIVE_VOLUME)
        )
        if confirmed_breakdown:
            broke_down_before_today = bool((recent["close"].iloc[:-1] < nearest_support.price).any()) if len(recent) > 1 else False
            touched_back_near_level = bool(
                (recent["high"] >= nearest_support.price * (1 - BREAKOUT_APPROACH_PCT / 100)).any()
            )
            state = (
                "retest_after_breakdown"
                if broke_down_before_today and touched_back_near_level
                else "confirmed_breakdown"
            )
            return make_reading("breakout_state", state, description, {"level": round(nearest_support.price, 4)})
        if 0 < pct_to_support <= BREAKOUT_APPROACH_PCT:
            return make_reading("breakout_state", "approaching_support", description, {"level": round(nearest_support.price, 4)})
        if float(recent["low"].min()) < nearest_support.price and current_price > nearest_support.price:
            return make_reading("breakout_state", "failed_breakdown", description, {"level": round(nearest_support.price, 4)})

    return make_reading("breakout_state", "no_breakout_signal", description, {})


def _gap_analysis(df: pd.DataFrame) -> list[FeatureReading]:
    if len(df) < 2:
        return [make_reading("latest_gap_pct", None, "Most recent session's open-vs-prior-close gap.", {}, status="insufficient_data")]

    prior_close = df["close"].iloc[-2]
    today_open = df["open"].iloc[-1]
    gap_pct = (today_open - prior_close) / prior_close * 100

    if gap_pct >= 0:
        gap_type = "gap_up"
    else:
        gap_type = "gap_down"

    magnitude = abs(gap_pct)
    if magnitude < 0.5:
        candidate = "common_gap"
    else:
        # Context: is price mid-trend (continuation), breaking out of a
        # recent range (breakaway), or extended after a big prior move
        # (exhaustion)? Simplified, documented heuristic per §6.5.
        lookback = df["close"].tail(20)
        recent_range_pct = (lookback.max() - lookback.min()) / lookback.mean() * 100
        prior_move_pct = (prior_close - df["close"].iloc[-11]) / df["close"].iloc[-11] * 100 if len(df) >= 11 else 0.0
        same_direction = (gap_pct > 0 and prior_move_pct > 0) or (gap_pct < 0 and prior_move_pct < 0)

        if recent_range_pct < 5 and magnitude >= 1.0:
            candidate = "breakaway_gap_candidate"
        elif same_direction and abs(prior_move_pct) >= 8:
            candidate = "exhaustion_gap_candidate"
        elif same_direction:
            candidate = "continuation_gap_candidate"
        else:
            candidate = "common_gap"

    # Gap-fill: how much of the gap has price retraced back toward prior close.
    post_gap_low = df["low"].iloc[-1]
    post_gap_high = df["high"].iloc[-1]
    if gap_pct > 0:
        filled_pct = max(0.0, min(100.0, (today_open - post_gap_low) / (today_open - prior_close) * 100)) if today_open != prior_close else 0.0
        gap_open = post_gap_low > prior_close  # still hasn't fully filled
    else:
        filled_pct = max(0.0, min(100.0, (post_gap_high - today_open) / (prior_close - today_open) * 100)) if today_open != prior_close else 0.0
        gap_open = post_gap_high < prior_close

    return [
        make_reading("latest_gap_pct", round(float(gap_pct), 4), "Most recent session's open-vs-prior-close gap, percent.", {}),
        make_reading("latest_gap_type", gap_type, "Direction of the most recent gap.", {}),
        make_reading("latest_gap_candidate_classification", candidate, "Rule-based gap-type candidate (not separately validated).", {}),
        make_reading("latest_gap_fill_pct", round(float(filled_pct), 2), "Percent of the most recent gap that has been filled by subsequent price action.", {}),
        make_reading("latest_gap_remains_open", bool(gap_open), "Whether the most recent gap has not yet been fully filled.", {}),
    ]


def _regime(df: pd.DataFrame, bars_used: int) -> FeatureReading:
    description = "Rule-based market regime classification (§6.6)."
    if bars_used < 200 + ADX_PERIOD:
        return make_reading("regime", None, description, {}, status="insufficient_data")

    closes = df["close"]
    sma20, sma50, sma200 = _sma(closes, 20), _sma(closes, 50), _sma(closes, 200)
    price = float(closes.iloc[-1])
    adx = _adx_series(df, ADX_PERIOD)
    atr_pct = _atr_pct_series(df, ATR_PERIOD)
    atr_percentile = _rolling_percentile(atr_pct, ATR_VOL_WINDOW)

    if pd.isna(adx.iloc[-1]) or pd.isna(atr_percentile.iloc[-1]) or pd.isna(sma200.iloc[-1]):
        return make_reading("regime", None, description, {}, status="insufficient_data")

    latest_adx = float(adx.iloc[-1])
    latest_atr_pctile = float(atr_percentile.iloc[-1])
    prior_atr_pctile = (
        float(atr_percentile.iloc[-1 - VOL_EXPANSION_LOOKBACK_BARS])
        if len(atr_percentile) > VOL_EXPANSION_LOOKBACK_BARS and not pd.isna(atr_percentile.iloc[-1 - VOL_EXPANSION_LOOKBACK_BARS])
        else latest_atr_pctile
    )
    delta = latest_atr_pctile - prior_atr_pctile

    if delta >= VOL_EXPANSION_DELTA:
        label = "volatility_expansion"
    elif delta <= -VOL_EXPANSION_DELTA:
        label = "volatility_contraction"
    else:
        uptrend_aligned = price > sma20.iloc[-1] > sma50.iloc[-1] > sma200.iloc[-1]
        downtrend_aligned = price < sma20.iloc[-1] < sma50.iloc[-1] < sma200.iloc[-1]

        if uptrend_aligned and latest_adx >= ADX_STRONG_TREND:
            label = "strong_uptrend"
        elif uptrend_aligned:
            label = "weak_uptrend"
        elif downtrend_aligned and latest_adx >= ADX_STRONG_TREND:
            label = "strong_downtrend"
        elif downtrend_aligned:
            label = "weak_downtrend"
        elif latest_atr_pctile >= SIDEWAYS_VOL_SPLIT_PERCENTILE:
            label = "sideways_high_volatility"
        else:
            label = "sideways_low_volatility"

    return make_reading(
        "regime",
        label,
        description,
        {"adx": round(latest_adx, 2), "atr_percentile": round(latest_atr_pctile, 2)},
        calculation_version=_CALC_VERSION,
    )


def compute_market_structure_snapshot(symbol: str, bars: list[OHLCVBar]) -> MarketStructureSnapshot:
    bars_used = len(bars)
    days_available = history_days_available(bars)

    if bars_used < SWING_LEFT_BARS + SWING_RIGHT_BARS + 1:
        return MarketStructureSnapshot(
            symbol=symbol.upper(),
            as_of=dt.datetime.now(dt.timezone.utc),
            bars_used=bars_used,
            history_days_available=days_available,
            swing_highs=[],
            swing_lows=[],
            trend_structure=make_reading("trend_structure", None, "Swing-based trend structure.", {}, status="insufficient_data"),
            consecutive_higher_highs=make_reading("consecutive_higher_highs", None, "", {}, status="insufficient_data"),
            consecutive_higher_lows=make_reading("consecutive_higher_lows", None, "", {}, status="insufficient_data"),
            consecutive_lower_highs=make_reading("consecutive_lower_highs", None, "", {}, status="insufficient_data"),
            consecutive_lower_lows=make_reading("consecutive_lower_lows", None, "", {}, status="insufficient_data"),
            bars_since_structural_change=make_reading("bars_since_structural_change", None, "", {}, status="insufficient_data"),
            support_resistance_levels=[],
            breakout_state=make_reading("breakout_state", None, "", {}, status="insufficient_data"),
            gap_readings=[],
            regime=make_reading("regime", None, "", {}, status="insufficient_data"),
            warnings=["Not enough bars for swing-point detection."],
        )

    df = bars_to_frame(bars)
    current_price = float(df["close"].iloc[-1])

    swing_highs, swing_lows = _swing_points(df)
    trend_structure, hh, hl, lh, ll, bars_since_change = _trend_structure(swing_highs, swing_lows, bars_used)
    levels = _support_resistance_levels(df, swing_highs, swing_lows, current_price)
    breakout_state = _breakout_state(df, levels, current_price)
    gap_readings = _gap_analysis(df)
    regime = _regime(df, bars_used)

    return MarketStructureSnapshot(
        symbol=symbol.upper(),
        as_of=dt.datetime.now(dt.timezone.utc),
        bars_used=bars_used,
        history_days_available=days_available,
        swing_highs=swing_highs,
        swing_lows=swing_lows,
        trend_structure=trend_structure,
        consecutive_higher_highs=hh,
        consecutive_higher_lows=hl,
        consecutive_lower_highs=lh,
        consecutive_lower_lows=ll,
        bars_since_structural_change=bars_since_change,
        support_resistance_levels=levels,
        breakout_state=breakout_state,
        gap_readings=gap_readings,
        regime=regime,
        warnings=[],
    )
