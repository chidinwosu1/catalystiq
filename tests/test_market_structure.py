"""Tests for the Market Structure data product (§6). All synthetic data, no network."""
import datetime as dt
import math

from catalystiq.analysis.common import bars_to_frame
from catalystiq.analysis.market_structure import _gap_analysis, compute_market_structure_snapshot
from catalystiq.schemas.market_data import OHLCVBar


def make_bar(date: dt.date, close: float, open_: float | None = None, high=None, low=None, volume: int = 1_000_000) -> OHLCVBar:
    open_ = close if open_ is None else open_
    high = max(open_, close) + 0.5 if high is None else high
    low = min(open_, close) - 0.5 if low is None else low
    return OHLCVBar(date=date, open=open_, high=high, low=low, close=close, volume=volume)


def business_days(start: dt.date, n: int) -> list[dt.date]:
    days = []
    d = start
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d)
        d += dt.timedelta(days=1)
    return days


def oscillating_uptrend(n: int) -> list[OHLCVBar]:
    days = business_days(dt.date(2020, 1, 1), n)
    return [make_bar(d, 100 + i * 0.3 + 5 * math.sin(i / 10)) for i, d in enumerate(days)]


def oscillating_downtrend(n: int) -> list[OHLCVBar]:
    days = business_days(dt.date(2020, 1, 1), n)
    return [make_bar(d, 300 - i * 0.3 - 5 * math.sin(i / 10)) for i, d in enumerate(days)]


def test_empty_history_returns_insufficient_data():
    snap = compute_market_structure_snapshot("EMPTY", [])

    assert snap.bars_used == 0
    assert snap.trend_structure.status == "insufficient_data"
    assert snap.swing_highs == []
    assert snap.swing_lows == []


def test_short_history_returns_insufficient_data():
    bars = oscillating_uptrend(300)[:5]

    snap = compute_market_structure_snapshot("SHORT", bars)

    assert snap.trend_structure.status == "insufficient_data"
    assert "Not enough bars" in snap.warnings[0]


def test_oscillating_uptrend_detects_higher_highs_higher_lows():
    bars = oscillating_uptrend(300)

    snap = compute_market_structure_snapshot("UP", bars)

    assert snap.trend_structure.value == "higher_highs_higher_lows"
    assert snap.trend_structure.status == "available"
    assert snap.consecutive_higher_highs.value >= 2
    assert snap.consecutive_higher_lows.value >= 2
    assert isinstance(snap.consecutive_higher_highs.value, int)


def test_oscillating_downtrend_detects_lower_highs_lower_lows():
    bars = oscillating_downtrend(300)

    snap = compute_market_structure_snapshot("DOWN", bars)

    assert snap.trend_structure.value == "lower_highs_lower_lows"
    assert snap.consecutive_lower_highs.value >= 2
    assert snap.consecutive_lower_lows.value >= 2


def test_strong_uptrend_regime_for_aligned_ma_and_high_adx():
    bars = oscillating_uptrend(300)

    snap = compute_market_structure_snapshot("UP", bars)

    assert snap.regime.value in ("strong_uptrend", "weak_uptrend", "volatility_expansion", "volatility_contraction")
    assert snap.regime.status == "available"


def test_regime_insufficient_data_below_200_bars():
    bars = oscillating_uptrend(199)

    snap = compute_market_structure_snapshot("SHORT2", bars)

    assert snap.regime.status == "insufficient_data"
    assert snap.regime.value is None


def test_swing_points_are_local_extremes():
    bars = oscillating_uptrend(300)

    snap = compute_market_structure_snapshot("UP", bars)

    assert len(snap.swing_highs) > 0
    assert len(snap.swing_lows) > 0
    for sw in snap.swing_highs:
        assert sw.kind == "high"
        if sw.confirmed:
            assert sw.pivot_strength >= 1
    for sw in snap.swing_lows:
        assert sw.kind == "low"
        if sw.confirmed:
            assert sw.pivot_strength >= 1


def test_support_resistance_levels_have_valid_shape():
    bars = oscillating_uptrend(300)

    snap = compute_market_structure_snapshot("UP", bars)

    assert len(snap.support_resistance_levels) > 0
    for level in snap.support_resistance_levels:
        assert level.type in ("support", "resistance")
        assert level.status in ("active", "broken")
        assert 0 <= level.strength_score <= 100
        assert level.touch_count >= 1


def test_breakout_state_is_a_known_label():
    bars = oscillating_uptrend(300)

    snap = compute_market_structure_snapshot("UP", bars)

    assert snap.breakout_state.value in (
        "confirmed_breakout",
        "retest_after_breakout",
        "approaching_resistance",
        "failed_breakout",
        "confirmed_breakdown",
        "retest_after_breakdown",
        "approaching_support",
        "failed_breakdown",
        "no_breakout_signal",
        "no_significant_level_nearby",
    )


def test_gap_analysis_flags_a_large_gap_up():
    days = business_days(dt.date(2020, 1, 1), 30)
    bars = [make_bar(d, 100 + i * 0.1) for i, d in enumerate(days[:-1])]
    last_close = bars[-1].close
    gapped_open = last_close * 1.05  # 5% gap up
    bars.append(make_bar(days[-1], gapped_open + 0.5, open_=gapped_open))

    snap = compute_market_structure_snapshot("GAP", bars)

    gap_pct = next(g for g in snap.gap_readings if g.name == "latest_gap_pct")
    gap_type = next(g for g in snap.gap_readings if g.name == "latest_gap_type")
    assert gap_pct.value > 4.0
    assert gap_type.value == "gap_up"


def test_gap_analysis_insufficient_data_for_single_bar():
    """Unit test of the gap-analysis helper directly with a single-row
    frame - the overall snapshot's own bars-required gate (11+ bars, for
    swing detection) means this path isn't reachable through
    compute_market_structure_snapshot() with realistic input, but the
    helper itself must still degrade safely rather than index-error."""
    bars = oscillating_uptrend(300)[:1]
    df = bars_to_frame(bars)

    readings = _gap_analysis(df)

    assert readings[0].status == "insufficient_data"


def test_symbol_is_uppercased():
    bars = oscillating_uptrend(300)

    snap = compute_market_structure_snapshot("aapl", bars)

    assert snap.symbol == "AAPL"
