"""Hand-computed checks for the TradingView-formula reference
implementations (catalystiq/validation/reference/tradingview_formulas.py)
- not circular against Catalyst IQ's own code."""
import numpy as np

from catalystiq.validation.reference import tradingview_formulas as tv


def test_relative_volume_hand_computed():
    # Prior 3 bars average 100; today's volume 150 -> 150.
    volume = np.array([90.0, 100.0, 110.0, 150.0])
    r = tv.relative_volume(volume, window=3)
    assert abs(r.value - 150.0) < 1e-9


def test_chaikin_money_flow_is_positive_when_closes_near_high():
    # Every bar closes at its high -> money flow multiplier is +1 every
    # bar -> CMF == 1.0.
    n = 25
    high = np.array([101.0] * n)
    low = np.array([99.0] * n)
    close = np.array([101.0] * n)  # closes at the high every bar
    volume = np.array([1000.0] * n)
    r = tv.chaikin_money_flow(high, low, close, volume, period=20)
    assert abs(r.value - 1.0) < 1e-9


def test_chaikin_money_flow_is_negative_when_closes_near_low():
    n = 25
    high = np.array([101.0] * n)
    low = np.array([99.0] * n)
    close = np.array([99.0] * n)  # closes at the low every bar
    volume = np.array([1000.0] * n)
    r = tv.chaikin_money_flow(high, low, close, volume, period=20)
    assert abs(r.value - (-1.0)) < 1e-9


def test_price_volume_trend_hand_traced():
    close = np.array([100.0, 110.0, 99.0])
    volume = np.array([1000.0, 500.0, 300.0])
    # bar1: 500 * (110-100)/100 = 50; bar2: 300 * (99-110)/110 = -30
    expected = 500.0 * 0.10 + 300.0 * ((99.0 - 110.0) / 110.0)
    r = tv.price_volume_trend(close, volume)
    assert abs(r.value - expected) < 1e-9


def test_historical_volatility_hand_computed():
    # Constant log-return each bar -> stdev of returns is exactly 0.
    close = np.array([100.0 * (1.01**i) for i in range(30)])
    r = tv.historical_volatility(close, window=20)
    assert abs(r.value - 0.0) < 1e-6


def test_pivot_points_detects_a_single_obvious_peak():
    # A clean symmetric peak at index 10 with 10 bars on each side.
    high = np.array([100.0 + min(i, 20 - i) for i in range(21)], dtype=float)
    low = high - 1.0
    result = tv.pivot_points(high, low, leftbars=5, rightbars=5)
    assert len(result.highs) == 1
    assert result.highs[0].index == 10
    assert result.highs[0].price == high[10]


def test_pivot_points_finds_no_pivots_in_a_monotonic_series():
    high = np.array([100.0 + i for i in range(20)], dtype=float)
    low = high - 1.0
    result = tv.pivot_points(high, low, leftbars=5, rightbars=5)
    assert result.highs == []
    assert result.lows == []


def test_insufficient_bars_returns_none():
    assert tv.relative_volume(np.array([1.0, 2.0]), window=20).value is None
    assert tv.chaikin_money_flow(
        np.array([1.0]), np.array([1.0]), np.array([1.0]), np.array([1.0]), period=20
    ).value is None
    assert tv.historical_volatility(np.array([1.0, 2.0]), window=20).value is None
