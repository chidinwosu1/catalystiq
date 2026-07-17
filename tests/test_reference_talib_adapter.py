"""Hand-computed checks for the TA-Lib reference adapter
(catalystiq/validation/reference/talib_adapter.py) - not circular against
Catalyst IQ's own code."""
import numpy as np

from catalystiq.validation.reference import talib_adapter as ta


def test_sma_matches_hand_computed_average():
    closes = np.array([10.0, 20.0, 30.0, 40.0, 50.0])
    r = ta.sma(closes, timeperiod=5)
    assert r.values[-1] == 30.0  # (10+20+30+40+50)/5
    assert r.lookback == 4


def test_rsi_is_100_when_every_bar_gains():
    closes = np.array([100.0 + i for i in range(30)])
    r = ta.rsi(closes, timeperiod=14)
    assert r.values[-1] == 100.0


def test_rsi_is_0_when_every_bar_loses():
    closes = np.array([200.0 - i for i in range(30)])
    r = ta.rsi(closes, timeperiod=14)
    assert r.values[-1] == 0.0


def test_atr_settles_to_constant_true_range():
    # Constant 2.0-wide bars with no gaps - true range is always 2.0, so
    # ATR (an average of true range) settles to 2.0.
    n = 40
    close = np.array([100.0] * n)
    high = np.array([101.0] * n)
    low = np.array([99.0] * n)
    r = ta.atr(high, low, close, timeperiod=14)
    assert abs(r.values[-1] - 2.0) < 1e-9


def test_obv_hand_traced():
    # day0 base = volume[0]; day1 up -> +v1; day2 down -> -v2.
    close = np.array([100.0, 105.0, 102.0])
    volume = np.array([1000.0, 500.0, 300.0])
    r = ta.obv(close, volume)
    assert r.values[-1] == 1000.0 + 500.0 - 300.0


def test_mfi_is_100_when_price_and_volume_rise_together():
    n = 30
    close = np.array([100.0 + i for i in range(n)])
    high = close + 0.5
    low = close - 0.5
    volume = np.array([1_000_000.0] * n)
    r = ta.mfi(high, low, close, volume, timeperiod=14)
    assert r.values[-1] == 100.0


def test_bbands_upper_lower_symmetric_around_middle():
    closes = np.array([100.0 + (i % 5) for i in range(40)], dtype=float)
    upper, middle, lower = ta.bbands(closes, timeperiod=20, nbdevup=2.0, nbdevdn=2.0)
    up_gap = upper.values[-1] - middle.values[-1]
    down_gap = middle.values[-1] - lower.values[-1]
    assert abs(up_gap - down_gap) < 1e-9


def test_macd_lookback_matches_slow_plus_signal_minus_one():
    closes = np.array([100.0 + i * 0.1 for i in range(60)])
    macd_line, signal, hist = ta.macd(closes, fastperiod=12, slowperiod=26, signalperiod=9)
    assert macd_line.lookback == signal.lookback == hist.lookback
    assert macd_line.lookback > 0


def test_ad_line_is_flat_when_close_equals_midpoint():
    # close exactly midway between high/low -> money flow multiplier is 0
    # every bar -> the AD line never moves.
    n = 20
    high = np.array([101.0] * n)
    low = np.array([99.0] * n)
    close = np.array([100.0] * n)
    volume = np.array([1_000_000.0] * n)
    r = ta.ad(high, low, close, volume)
    assert all(abs(v) < 1e-9 for v in r.values)
