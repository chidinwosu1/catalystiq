"""Composite, decision-rule Catalyst IQ outputs (market regime, trend
structure, breakout state, liquidity classification) have no single
universal external reference value - no TA-Lib function or TradingView
built-in computes "is this a strong uptrend." These are validated via
documented decision rules + synthetic scenarios built to unambiguously
satisfy one rule branch, per catalystiq/validation/reference/
composite_scenarios.py, rather than via numeric tolerance comparison.
"""
from catalystiq.analysis.market_structure import compute_market_structure_snapshot
from catalystiq.analysis.volume_liquidity import compute_volume_liquidity_snapshot
from catalystiq.validation.reference import composite_scenarios as scenarios


def test_strong_uptrend_regime():
    snap = compute_market_structure_snapshot("UP", scenarios.strong_uptrend_scenario())
    assert snap.regime.value == "strong_uptrend"


def test_strong_downtrend_regime():
    snap = compute_market_structure_snapshot("DOWN", scenarios.strong_downtrend_scenario())
    assert snap.regime.value == "strong_downtrend"


def test_sideways_low_volatility_regime():
    snap = compute_market_structure_snapshot("FLAT", scenarios.sideways_low_volatility_scenario())
    assert snap.regime.value == "sideways_low_volatility"


def test_higher_highs_higher_lows_trend_structure():
    snap = compute_market_structure_snapshot("HH", scenarios.higher_highs_higher_lows_scenario())
    assert snap.trend_structure.value == "higher_highs_higher_lows"


def test_lower_highs_lower_lows_trend_structure():
    snap = compute_market_structure_snapshot("LL", scenarios.lower_highs_lower_lows_scenario())
    assert snap.trend_structure.value == "lower_highs_lower_lows"


def test_range_bound_trend_structure():
    snap = compute_market_structure_snapshot("RANGE", scenarios.range_bound_scenario())
    assert snap.trend_structure.value == "range_bound"


def test_failed_breakout_state():
    snap = compute_market_structure_snapshot("FAIL", scenarios.failed_breakout_scenario())
    assert snap.breakout_state.value == "failed_breakout"


def test_high_liquidity_classification():
    snap = compute_volume_liquidity_snapshot("BIG", scenarios.high_liquidity_scenario())
    assert snap.liquidity_classification.value == "high"


def test_very_low_liquidity_classification():
    snap = compute_volume_liquidity_snapshot("TINY", scenarios.very_low_liquidity_scenario())
    assert snap.liquidity_classification.value == "very_low"
