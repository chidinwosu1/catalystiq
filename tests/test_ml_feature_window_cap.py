"""Opt-in feature-window cap: equivalence vs full point-in-time history.

The cap (``SilverPointInTimeProvider(max_feature_bars=N)``) exists only to bound
per-example CPU on long histories. It MUST NOT change any feature value. These
tests prove that across every currently registered feature, for several symbols,
prediction dates, and market regimes - not just the final opportunity score.

Equivalence has three honestly-different tiers, all asserted here:

  * BIT-IDENTICAL: features computed from the same tail elements by simple
    arithmetic (log/window returns, gaps, OHLCV), the bucketed integer
    rule-based factors, the market-regime code, and - critically - beta and the
    benchmark-relative returns. beta is a full-history statistic (cov/var over
    all aligned returns), so the provider feeds its risk snapshot the UNCAPPED
    series on purpose; the test confirms it stays identical.
  * FP-EQUAL (~1e-13): the pandas ``rolling`` indicators (SMA family, realized
    vol, relative volume). ``rolling`` keeps a running accumulator, so the same
    trailing window differs only by floating-point summation order between a
    500- and a 900-length series - a rounding artifact, not a real change.
  * CONVERGENT (~1e-12): RSI / MACD / ATR use ``ewm(adjust=False)`` (an IIR
    recursion over the whole series). A last-N cap changes their warm-up seed,
    so they are convergent, not exact. The test asserts a strict bound and never
    claims bit-identity for these five.

No DB/network: an injected bars_loader serves synthetic bars (unit-test only).
"""
import datetime as dt
import math

import pytest

from catalystiq.ml.features.pit_provider import (
    LONGEST_FEATURE_LOOKBACK_BARS,
    SilverPointInTimeProvider,
)
from catalystiq.ml.features.regime import classify_market_regime
from catalystiq.ml.features.schema import DataQualityStatus
from catalystiq.schemas.market_data import OHLCVBar

CAP = 500

# adjust=False EMA/IIR indicators: convergent under a cap, never bit-identical.
_EMA_CONVERGENT = {"rsi_14", "macd", "macd_signal", "macd_hist", "atr_14"}

# pandas rolling() indicators: identical trailing window, but a running
# accumulator makes the result differ by floating-point summation order (~1e-13)
# between a 500- and a 900-length series. Real value unchanged.
_ROLLING_WINDOWED = {"sma_20", "sma_50", "sma_200", "price_vs_sma_50",
                     "sma_50_slope", "realized_vol_20d", "relative_volume_20d"}

# Features the user explicitly required to remain IDENTICAL and point-in-time
# safe under the cap (beta + benchmark-relative). These use the same tail
# elements by simple arithmetic (or the uncapped series, for beta) -> exact.
_BENCHMARK_RELATIVE = {"beta_60d", "market_return_20d", "sector_return_20d",
                       "relative_strength_60d"}

_EMA_TOL = 1e-6      # strict bound; observed ~1e-12 at 500 bars
_ROLLING_TOL = 1e-9  # strict bound; observed ~1e-13 (FP summation order only)


def _weekday_series(*, n, start_year, seed, drift, vol, phase=0.0):
    """A long, deterministic weekday OHLCV series (no Math.random)."""
    bars = []
    d = dt.date(start_year, 1, 2)
    p = 100.0 * seed
    i = 0
    while len(bars) < n:
        if d.weekday() < 5:
            p *= 1 + drift + vol * math.sin(i / 13.0 + phase)
            bars.append(OHLCVBar(
                date=d, open=p * 0.996, high=p * 1.011, low=p * 0.988,
                close=p, volume=1_000_000 + (i % 50) * 1000))
            i += 1
        d += dt.timedelta(days=1)
    return bars


# Starts in 2016 so the full as-of history at every prediction date below is
# ~850-1100 sessions - the 500-bar cap therefore truncates HARD (300-600 old
# bars dropped). That is a genuine test that even structure-derived features
# (support/resistance distances), which could in principle reference old swings,
# do not shift under the cap.
_N = 1250
_START_YEAR = 2016

# Three benchmark shapes -> three distinct market regimes exercised.
_BENCHMARKS = {
    "bull_calm": dict(seed=4.0, drift=0.0009, vol=0.004),
    "bear": dict(seed=4.0, drift=-0.0011, vol=0.006),
    "volatile": dict(seed=4.0, drift=0.0001, vol=0.022),
}

_ASSETS = {
    "AAA": dict(seed=1.0, drift=0.0006, vol=0.010, phase=0.4),
    "BBB": dict(seed=2.0, drift=-0.0004, vol=0.013, phase=1.1),
    "CCC": dict(seed=3.0, drift=0.0002, vol=0.018, phase=2.0),
}

# Prediction timestamps chosen so full as-of history exceeds the cap at each.
_DATES = [
    dt.datetime(2020, 6, 1, 20, 0, 0),   # as-of ~1100 sessions
    dt.datetime(2019, 6, 3, 20, 0, 0),   # as-of ~850 sessions
]

# A curated (regime, symbol, date) matrix that covers every regime, every
# symbol, and both dates while keeping the number of (expensive) full-history
# builds small. Each triple triggers one full build and one capped build.
_MATRIX = [
    ("bull_calm", "AAA", _DATES[0]),
    ("bull_calm", "BBB", _DATES[1]),
    ("bear", "CCC", _DATES[0]),
    ("bear", "AAA", _DATES[1]),
    ("volatile", "BBB", _DATES[0]),
    ("volatile", "CCC", _DATES[1]),
]


def _data(bench_kwargs):
    data = {
        "SPY": _weekday_series(n=_N, start_year=_START_YEAR, **bench_kwargs),
        "XLK": _weekday_series(n=_N, start_year=_START_YEAR, seed=2.5, drift=0.0005, vol=0.009, phase=0.7),
    }
    for sym, kw in _ASSETS.items():
        data[sym] = _weekday_series(n=_N, start_year=_START_YEAR, **kw)
    return data


def _providers(data):
    common = dict(
        db=None, benchmark_symbol="SPY",
        sector_resolver=lambda s: "XLK",
        bars_loader=lambda s, db: data.get(s.upper(), []),
    )
    full = SilverPointInTimeProvider(**common)
    capped = SilverPointInTimeProvider(max_feature_bars=CAP, **common)
    return full, capped


def _by_name(feats):
    return {f.feature_name: f for f in feats}


# --------------------------------------------------------------------------- #
# Core equivalence: every registered feature, across the symbol/date/regime
# matrix. This is the primary proof the cap changes no feature.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("regime_name, sym, pt", _MATRIX)
def test_capped_equals_full_for_every_registered_feature(regime_name, sym, pt):
    data = _data(_BENCHMARKS[regime_name])
    full_prov, capped_prov = _providers(data)

    full = _by_name(full_prov.get_features(sym, pt))
    capped = _by_name(capped_prov.get_features(sym, pt))

    # Identical feature SET and identical availability for every name.
    assert set(full) == set(capped), (regime_name, sym, pt)

    checked_exact = 0
    for name, ff in full.items():
        cf = capped[name]
        assert ff.data_quality_status == cf.data_quality_status, \
            f"availability changed for {name} ({regime_name}/{sym})"
        fv, cv = ff.feature_value, cf.feature_value
        if fv is None or cv is None:
            assert fv is None and cv is None, f"{name} None-ness diverged"
            continue
        if isinstance(fv, float) and isinstance(cv, float):
            dev = abs(fv - cv)
            if name in _EMA_CONVERGENT:
                assert dev <= _EMA_TOL * max(1.0, abs(fv)), \
                    f"{name} EMA deviation {dev} exceeds tolerance"
            elif name in _ROLLING_WINDOWED:
                assert dev <= _ROLLING_TOL * max(1.0, abs(fv)), \
                    f"{name} rolling deviation {dev} exceeds tolerance"
            else:
                # Simple-arithmetic / rule-based / regime / beta /
                # benchmark-relative / support-resistance: bit-identical (no
                # warm-up, no rolling accumulator).
                assert fv == cv, f"{name} changed under cap: {fv} != {cv}"
                checked_exact += 1
        else:
            assert fv == cv, f"{name} changed under cap: {fv!r} != {cv!r}"

    # We actually exercised a meaningful number of bit-exact features.
    assert checked_exact > 0


# --------------------------------------------------------------------------- #
# The user's explicit requirement: beta + benchmark-relative stay identical.
# --------------------------------------------------------------------------- #
def test_beta_and_benchmark_relative_identical_and_point_in_time():
    data = _data(_BENCHMARKS["bull_calm"])
    full_prov, capped_prov = _providers(data)
    pt = _DATES[-1]

    full = _by_name(full_prov.get_features("AAA", pt))
    capped = _by_name(capped_prov.get_features("AAA", pt))

    for name in _BENCHMARK_RELATIVE:
        assert name in full and name in capped
        fv, cv = full[name].feature_value, capped[name].feature_value
        assert fv is not None, f"{name} should be populated with full context"
        assert fv == cv, f"{name} must be identical under the cap ({fv} != {cv})"
        assert full[name].data_quality_status is DataQualityStatus.OK

    # beta is a full-history statistic: capping its input WOULD change it, which
    # is exactly why the provider feeds the risk snapshot the uncapped series.
    beta = capped["beta_60d"]
    assert beta.available_at_timestamp <= beta.prediction_timestamp  # PIT provenance


# --------------------------------------------------------------------------- #
# Multiple regimes are genuinely exercised (the matrix isn't all one code path).
# The regime CODE's exactness under the cap is asserted in the main matrix test.
# --------------------------------------------------------------------------- #
def test_distinct_regimes_are_exercised():
    seen_codes = set()
    for regime_name in _BENCHMARKS:
        data = _data(_BENCHMARKS[regime_name])
        # Classify on the benchmark truncated to a point-in-time window (>=200).
        reg = classify_market_regime(data["SPY"][:700], symbol="SPY")
        if reg.available:
            seen_codes.add(reg.code)
    # At least two different regime codes were produced by the three shapes.
    assert len(seen_codes) >= 2


# --------------------------------------------------------------------------- #
# The cap is opt-in and fails closed below the longest registered lookback.
# --------------------------------------------------------------------------- #
def test_cap_below_longest_lookback_fails_closed():
    with pytest.raises(ValueError, match="registered-feature lookback"):
        SilverPointInTimeProvider(db=None, max_feature_bars=LONGEST_FEATURE_LOOKBACK_BARS - 1)


def test_cap_at_or_above_floor_is_accepted():
    # Exactly at the floor is allowed (sma_200 still gets its 200 sessions).
    SilverPointInTimeProvider(db=None, max_feature_bars=LONGEST_FEATURE_LOOKBACK_BARS)
    SilverPointInTimeProvider(db=None, max_feature_bars=CAP)


def test_default_is_full_history_no_cap():
    prov = SilverPointInTimeProvider(db=None)
    assert prov.max_feature_bars is None


# --------------------------------------------------------------------------- #
# The cap is a feature-computation optimization ONLY: it must never touch the
# executable-entry / forward-path (label) machinery, which reads full bars.
# --------------------------------------------------------------------------- #
def test_cap_does_not_change_executable_entry_or_forward_path():
    data = _data(_BENCHMARKS["bull_calm"])
    full_prov, capped_prov = _providers(data)
    pt = _DATES[0]
    assert full_prov.get_executable_entry("AAA", pt) == capped_prov.get_executable_entry("AAA", pt)
    entry = full_prov.get_executable_entry("AAA", pt)
    assert entry is not None
    fp_full = full_prov.get_forward_path("AAA", entry[0], 10)
    fp_capped = capped_prov.get_forward_path("AAA", entry[0], 10)
    assert [b.session for b in fp_full] == [b.session for b in fp_capped]
    assert [b.close for b in fp_full] == [b.close for b in fp_capped]
