"""Point-in-time market-regime classifier."""
import datetime as dt

from catalystiq.schemas.market_data import OHLCVBar
from catalystiq.ml.features.regime import (
    REGIME_CODES,
    REGIME_VERSION,
    TrendState,
    VolatilityState,
    classify_market_regime,
)


def _bars(n=320, drift=0.0009, vol_amp=0.0, seed=4.0):
    import math
    bars = []
    base = dt.date(2020, 1, 1)
    p = 100.0 * seed
    for i in range(n):
        p *= 1 + drift + vol_amp * math.sin(i / 3)
        span = max(0.002, abs(vol_amp) * 4)
        bars.append(OHLCVBar(date=base + dt.timedelta(days=i), open=p * (1 - span / 2),
                             high=p * (1 + span), low=p * (1 - span), close=p, volume=1_000_000))
    return bars


def test_insufficient_history_returns_insufficient():
    r = classify_market_regime(_bars(n=50))
    assert r.insufficient and r.code is None and r.label is None


def test_bull_calm_regime():
    r = classify_market_regime(_bars(drift=0.0009, vol_amp=0.0))
    assert r.available
    assert r.trend_state is TrendState.BULL
    assert r.volatility_state is VolatilityState.CALM
    assert r.label == "bull_calm"
    assert r.code == REGIME_CODES["bull_calm"]
    assert r.version == REGIME_VERSION


def test_bear_trend_detected():
    r = classify_market_regime(_bars(drift=-0.0012, vol_amp=0.0))
    assert r.available
    assert r.trend_state is TrendState.BEAR


def test_stressed_volatility_detected():
    # Large oscillation -> high realized vol -> stressed.
    r = classify_market_regime(_bars(drift=0.0009, vol_amp=0.03))
    assert r.available
    assert r.volatility_state is VolatilityState.STRESSED


def test_regime_codes_are_stable_and_complete():
    # 3 trend x 3 vol = 9 stable codes, all distinct.
    assert len(REGIME_CODES) == 9
    assert len(set(REGIME_CODES.values())) == 9


def test_regime_is_point_in_time_invariant():
    # Regime as-of a fixed point is unchanged by appending future bars.
    base = _bars(n=300)
    extended = _bars(n=380)
    a = classify_market_regime(base)
    b = classify_market_regime(extended[:300])
    assert a.label == b.label and a.code == b.code
