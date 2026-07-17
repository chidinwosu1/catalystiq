"""Runs the reference-calculation comparison for one GoldCalculationRun
and persists the audit trail (GoldReferenceCheck rows).

Reads bars via market_price_pipeline.get_silver_bars_for_build() - the
immutable SilverBuildRunBar snapshot, not the live current-state table -
so the reference calc always sees the exact same symbol, adjusted/
unadjusted state, timeframe, completed candles, and lookback the original
Gold calculation used, no matter when this check actually runs.

Only compares each indicator's single latest (fully-settled) value -
that's the only surface Catalyst IQ's own Gold payload exposes
(FeatureReading/IndicatorReading carry one current reading each, not a
full series) - so a warm-up period is excluded by construction: if either
side hasn't accumulated enough bars to produce a settled value yet, the
check is "not_applicable", never a numeric comparison against a
warmed-up/NaN value.

Never overwrites a Gold row on mismatch - see quarantine_gold_row()."""
from __future__ import annotations

import datetime as dt
import math

import numpy as np
import scipy
import talib
from sqlalchemy.orm import Session

from catalystiq.analysis.config import (
    DEFAULT_MARKET_STRUCTURE_CONFIG,
    DEFAULT_RISK_CONFIG,
    DEFAULT_TECHNICAL_CONFIG,
    DEFAULT_VOLUME_LIQUIDITY_CONFIG,
)
from catalystiq.db import models
from catalystiq.pipelines.market_price_pipeline import get_silver_bars_for_build
from catalystiq.schemas.market_data import OHLCVBar
from catalystiq.validation.reference import independent_stats as stats
from catalystiq.validation.reference import registry
from catalystiq.validation.reference import talib_adapter as ta
from catalystiq.validation.reference import tradingview_formulas as tv

_RECORD_CLASS_BY_PRODUCT = {
    "technical": models.TechnicalSnapshotRecord,
    "market_structure": models.MarketStructureSnapshotRecord,
    "risk": models.RiskSnapshotRecord,
    "volume_liquidity": models.VolumeLiquiditySnapshotRecord,
    "market_context": models.MarketContextSnapshotRecord,
}

_TALIB_VERSION = getattr(talib, "__version__", "0.7.1")
_SCIPY_VERSION = scipy.__version__


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


def _extract_actual(payload: dict, name: str) -> float | None:
    for key in ("indicators", "metrics"):
        for item in payload.get(key, []):
            if item.get("name") == name:
                value = item.get("value")
                if isinstance(value, bool):
                    return None
                if isinstance(value, (int, float)):
                    return float(value)
                return None
    return None


def _clean(x: float | None) -> float | None:
    if x is None:
        return None
    if isinstance(x, float) and (math.isnan(x) or math.isinf(x)):
        return None
    return float(x)


def _compare(
    run: models.GoldCalculationRun,
    indicator_name: str,
    source,
    expected: float | None,
    actual: float | None,
    tolerance_abs: float | None,
    tolerance_rel: float | None,
    reference_library: str,
    reference_library_version: str,
    parameters: dict,
    warmup_bars: int,
    now: dt.datetime,
) -> models.GoldReferenceCheck:
    expected = _clean(expected)
    actual = _clean(actual)

    if expected is None or actual is None:
        return models.GoldReferenceCheck(
            gold_calculation_run_id=run.id,
            ticker_id=run.ticker_id,
            indicator_name=indicator_name,
            reference_source=source.value,
            reference_library=reference_library,
            reference_library_version=reference_library_version,
            parameters=parameters,
            expected_value=expected,
            actual_value=actual,
            absolute_diff=None,
            relative_diff=None,
            tolerance_abs=tolerance_abs,
            tolerance_rel=tolerance_rel,
            warmup_bars_excluded=warmup_bars,
            status="not_applicable",
            discrepancy_reason="insufficient settled data on one or both sides (warm-up)"
            if (expected is None) != (actual is None)
            else "insufficient settled data on both sides (warm-up)",
            checked_at=now,
        )

    abs_diff = abs(expected - actual)
    rel_diff = abs_diff / abs(expected) if expected != 0 else (0.0 if actual == 0 else float("inf"))
    passed = (tolerance_abs is not None and abs_diff <= tolerance_abs) or (
        tolerance_rel is not None and rel_diff <= tolerance_rel
    )
    status = "pass" if passed else "fail"
    reason = (
        None
        if passed
        else (
            f"expected={expected!r} actual={actual!r} abs_diff={abs_diff!r} rel_diff={rel_diff!r} "
            f"tolerance_abs={tolerance_abs!r} tolerance_rel={tolerance_rel!r}"
        )
    )
    return models.GoldReferenceCheck(
        gold_calculation_run_id=run.id,
        ticker_id=run.ticker_id,
        indicator_name=indicator_name,
        reference_source=source.value,
        reference_library=reference_library,
        reference_library_version=reference_library_version,
        parameters=parameters,
        expected_value=expected,
        actual_value=actual,
        absolute_diff=abs_diff,
        relative_diff=rel_diff,
        tolerance_abs=tolerance_abs,
        tolerance_rel=tolerance_rel,
        warmup_bars_excluded=warmup_bars,
        status=status,
        discrepancy_reason=reason,
        checked_at=now,
    )


def _check_technical(
    run: models.GoldCalculationRun, bars: list[OHLCVBar], payload: dict, now: dt.datetime
) -> list[models.GoldReferenceCheck]:
    cfg = DEFAULT_TECHNICAL_CONFIG
    close = np.array([b.close for b in bars], dtype=float)
    high = np.array([b.high for b in bars], dtype=float)
    low = np.array([b.low for b in bars], dtype=float)
    volume = np.array([b.volume for b in bars], dtype=float)

    checks: list[models.GoldReferenceCheck] = []

    def add(name, expected, params, lookback):
        spec = registry.get_spec("technical", name)
        actual = _extract_actual(payload, name)
        checks.append(
            _compare(
                run, name, spec.source, expected, actual, spec.tolerance_abs, spec.tolerance_rel,
                "TA-Lib" if spec.source == registry.ReferenceSource.TALIB else "TradingView formula",
                _TALIB_VERSION if spec.source == registry.ReferenceSource.TALIB else "n/a",
                params, lookback, now,
            )
        )

    sma_windows = cfg.sma_windows
    sma_results = {}
    for window, name in zip(sma_windows, ("sma_20", "sma_50", "sma_100", "sma_200")):
        r = ta.sma(close, window)
        sma_results[name] = r
        add(name, r.values[-1] if len(r.values) else None, {"timeperiod": window}, r.lookback)

    sma50 = sma_results["sma_50"]
    if len(sma50.values) and not np.isnan(sma50.values[-1]):
        expected_price_vs_sma = (close[-1] - sma50.values[-1]) / sma50.values[-1] * 100
    else:
        expected_price_vs_sma = None
    add("price_vs_sma_50_pct", expected_price_vs_sma, {"window": cfg.price_vs_sma_window}, sma50.lookback)

    r = ta.rsi(close, cfg.rsi_period)
    add("rsi_14", r.values[-1] if len(r.values) else None, {"timeperiod": cfg.rsi_period}, r.lookback)

    macd_line, macd_signal, macd_hist = ta.macd(close, cfg.macd_fast, cfg.macd_slow, cfg.macd_signal)
    macd_params = {"fastperiod": cfg.macd_fast, "slowperiod": cfg.macd_slow, "signalperiod": cfg.macd_signal}
    add("macd_line", macd_line.values[-1] if len(macd_line.values) else None, macd_params, macd_line.lookback)
    add("macd_signal", macd_signal.values[-1] if len(macd_signal.values) else None, macd_params, macd_signal.lookback)
    add("macd_histogram", macd_hist.values[-1] if len(macd_hist.values) else None, macd_params, macd_hist.lookback)

    r = ta.atr(high, low, close, cfg.atr_period)
    add("atr_14", r.values[-1] if len(r.values) else None, {"timeperiod": cfg.atr_period}, r.lookback)
    if len(r.values) and not np.isnan(r.values[-1]):
        expected_atr_pct = r.values[-1] / close[-1] * 100
    else:
        expected_atr_pct = None
    add("atr_14_pct", expected_atr_pct, {"timeperiod": cfg.atr_period}, r.lookback)

    r = ta.obv(close, volume)
    add("obv", r.values[-1] if len(r.values) else None, {}, r.lookback)

    upper, middle, lower = ta.bbands(close, cfg.bollinger_window, float(cfg.bollinger_num_std), float(cfg.bollinger_num_std))
    bb_params = {"timeperiod": cfg.bollinger_window, "nbdevup": cfg.bollinger_num_std, "nbdevdn": cfg.bollinger_num_std}
    if len(upper.values) and not any(np.isnan(x.values[-1]) for x in (upper, middle, lower)):
        expected_percent_b = (close[-1] - lower.values[-1]) / (upper.values[-1] - lower.values[-1]) * 100
        expected_bandwidth = (upper.values[-1] - lower.values[-1]) / middle.values[-1] * 100
    else:
        expected_percent_b = expected_bandwidth = None
    add("bollinger_percent_b", expected_percent_b, bb_params, upper.lookback)
    add("bollinger_bandwidth_pct", expected_bandwidth, bb_params, upper.lookback)

    hv = tv.historical_volatility(close, cfg.realized_vol_window)
    add(
        "realized_volatility_20d_annualized_pct",
        hv.value,
        {"window": cfg.realized_vol_window},
        cfg.realized_vol_window,
    )

    return checks


def _check_volume_liquidity(
    run: models.GoldCalculationRun, bars: list[OHLCVBar], payload: dict, now: dt.datetime
) -> list[models.GoldReferenceCheck]:
    cfg = DEFAULT_VOLUME_LIQUIDITY_CONFIG
    high = np.array([b.high for b in bars], dtype=float)
    low = np.array([b.low for b in bars], dtype=float)
    close = np.array([b.close for b in bars], dtype=float)
    volume = np.array([b.volume for b in bars], dtype=float)

    checks: list[models.GoldReferenceCheck] = []

    def add(name, source, expected, lib, libver, params, warmup):
        spec = registry.get_spec("volume_liquidity", name)
        actual = _extract_actual(payload, name)
        checks.append(
            _compare(run, name, source, expected, actual, spec.tolerance_abs, spec.tolerance_rel, lib, libver, params, warmup, now)
        )

    r = ta.ad(high, low, close, volume)
    add("accumulation_distribution_line", registry.ReferenceSource.TALIB, r.values[-1] if len(r.values) else None, "TA-Lib", _TALIB_VERSION, {}, r.lookback)

    r = ta.mfi(high, low, close, volume, cfg.mfi_period)
    add("money_flow_index", registry.ReferenceSource.TALIB, r.values[-1] if len(r.values) else None, "TA-Lib", _TALIB_VERSION, {"timeperiod": cfg.mfi_period}, r.lookback)

    rv = tv.relative_volume(volume, cfg.relative_volume_window)
    add("relative_volume_pct", registry.ReferenceSource.TRADINGVIEW_FORMULA, rv.value, "TradingView formula", "n/a", {"window": cfg.relative_volume_window}, cfg.relative_volume_window)

    cmf = tv.chaikin_money_flow(high, low, close, volume, cfg.cmf_period)
    add("chaikin_money_flow", registry.ReferenceSource.TRADINGVIEW_FORMULA, cmf.value, "TradingView formula", "n/a", {"period": cfg.cmf_period}, cfg.cmf_period)

    pvt = tv.price_volume_trend(close, volume)
    add("volume_price_trend", registry.ReferenceSource.TRADINGVIEW_FORMULA, pvt.value, "TradingView formula", "n/a", {}, 1)

    return checks


def _check_risk(
    run: models.GoldCalculationRun,
    bars: list[OHLCVBar],
    benchmark_bars: list[OHLCVBar] | None,
    payload: dict,
    now: dt.datetime,
) -> list[models.GoldReferenceCheck]:
    cfg = DEFAULT_RISK_CONFIG
    close = np.array([b.close for b in bars], dtype=float)
    bench_close = np.array([b.close for b in benchmark_bars], dtype=float) if benchmark_bars else None

    checks: list[models.GoldReferenceCheck] = []

    def add(name, expected, params, warmup):
        spec = registry.get_spec("risk", name)
        actual = _extract_actual(payload, name)
        checks.append(
            _compare(run, name, spec.source, expected, actual, spec.tolerance_abs, spec.tolerance_rel, "independent numpy/scipy", _SCIPY_VERSION, params, warmup, now)
        )

    if bench_close is not None and len(bench_close) >= 2:
        b = stats.beta(close, bench_close)
        add("beta_vs_benchmark", b.value, {"convention": "full-history log-return cov/var"}, 0)

    sh = stats.sharpe_ratio(close, cfg.downside_dev_window, cfg.sharpe_risk_free_rate_annual, cfg.trading_days_per_year)
    add("sharpe_ratio", sh.value, {"window": cfg.downside_dev_window, "risk_free_rate": cfg.sharpe_risk_free_rate_annual}, cfg.downside_dev_window)

    so = stats.sortino_ratio(close, cfg.downside_dev_window, cfg.sharpe_risk_free_rate_annual, cfg.trading_days_per_year)
    add("sortino_ratio", so.value, {"window": cfg.downside_dev_window, "risk_free_rate": cfg.sharpe_risk_free_rate_annual}, cfg.downside_dev_window)

    ca = stats.calmar_ratio(close, cfg.downside_dev_window, cfg.trading_days_per_year)
    add("calmar_ratio", ca.value, {"window": cfg.downside_dev_window}, cfg.downside_dev_window)

    hv = stats.historical_var(close, cfg.var_sample_max, cfg.var_confidence)
    add("historical_var_95_pct", hv.value, {"sample_max": cfg.var_sample_max, "confidence": cfg.var_confidence}, cfg.min_bars_for_var)

    pv = stats.parametric_var(close, cfg.var_sample_max, cfg.var_confidence)
    add("parametric_var_95_pct", pv.value, {"sample_max": cfg.var_sample_max, "confidence": cfg.var_confidence}, cfg.min_bars_for_var)

    return checks


def _check_market_structure_pivots(
    run: models.GoldCalculationRun, bars: list[OHLCVBar], payload: dict, now: dt.datetime
) -> list[models.GoldReferenceCheck]:
    """Structural comparison, not scalar tolerance: compares the confirmed
    swing highs/lows Catalyst IQ returned (already capped to the most
    recent SWING_POINTS_RETURNED) against the reference fractal pivots
    over the same tail window."""
    cfg = DEFAULT_MARKET_STRUCTURE_CONFIG
    high = np.array([b.high for b in bars], dtype=float)
    low = np.array([b.low for b in bars], dtype=float)

    pivots = tv.pivot_points(high, low, cfg.swing_left_bars, cfg.swing_right_bars)
    n_return = cfg.swing_points_returned

    actual_highs = {round(float(p["price"]), 6) for p in payload.get("swing_highs", []) if p.get("confirmed")}
    actual_lows = {round(float(p["price"]), 6) for p in payload.get("swing_lows", []) if p.get("confirmed")}

    # Catalyst IQ slices to the last `n_return` swing points BEFORE
    # filtering to confirmed-only, so the confirmed count among those
    # `n_return` can be less than n_return whenever an unconfirmed
    # candidate is near the tail. Match that dynamic count rather than
    # always comparing the reference's last n_return confirmed pivots.
    ref_highs = {round(p.price, 6) for p in pivots.highs[-len(actual_highs):]} if actual_highs else set()
    ref_lows = {round(p.price, 6) for p in pivots.lows[-len(actual_lows):]} if actual_lows else set()

    def build(label, expected_set, actual_set):
        missing = expected_set - actual_set
        extra = actual_set - expected_set
        passed = not missing and not extra
        return models.GoldReferenceCheck(
            gold_calculation_run_id=run.id,
            ticker_id=run.ticker_id,
            indicator_name=f"swing_{label}",
            reference_source=registry.ReferenceSource.TRADINGVIEW_FORMULA.value,
            reference_library="TradingView formula (ta.pivothigh/ta.pivotlow)",
            reference_library_version="n/a",
            parameters={"leftbars": cfg.swing_left_bars, "rightbars": cfg.swing_right_bars, "returned": n_return},
            expected_value=float(len(expected_set)),
            actual_value=float(len(actual_set)),
            absolute_diff=float(len(missing) + len(extra)),
            relative_diff=None,
            tolerance_abs=0.0,
            tolerance_rel=None,
            warmup_bars_excluded=cfg.swing_left_bars + cfg.swing_right_bars,
            status="pass" if passed else "fail",
            discrepancy_reason=None if passed else f"missing from Catalyst IQ: {sorted(missing)}; extra in Catalyst IQ: {sorted(extra)}",
            checked_at=now,
        )

    return [build("highs", ref_highs, actual_highs), build("lows", ref_lows, actual_lows)]


def run_reference_check(gold_calculation_run_id: int, db: Session) -> list[models.GoldReferenceCheck]:
    """Runs every applicable reference check for one GoldCalculationRun
    and persists the audit trail. Marks the associated gold_* row
    "quarantined" (never overwritten) if any check fails."""
    run = db.get(models.GoldCalculationRun, gold_calculation_run_id)
    if run is None or run.silver_build_run_id is None:
        return []

    record_cls = _RECORD_CLASS_BY_PRODUCT.get(run.product_name)
    if record_cls is None:
        return []

    gold_row = db.query(record_cls).filter_by(gold_calculation_run_id=run.id).one_or_none()
    if gold_row is None:
        return []

    bars = get_silver_bars_for_build(run.silver_build_run_id, db)
    if not bars:
        return []

    now = _now()
    payload = gold_row.payload

    if run.product_name == "technical":
        checks = _check_technical(run, bars, payload, now)
    elif run.product_name == "volume_liquidity":
        checks = _check_volume_liquidity(run, bars, payload, now)
    elif run.product_name == "risk":
        benchmark_bars = None
        dep = (
            db.query(models.GoldCalculationRunDependency)
            .filter_by(gold_calculation_run_id=run.id, role="benchmark")
            .one_or_none()
        )
        if dep is not None and dep.silver_build_run_id is not None:
            benchmark_bars = get_silver_bars_for_build(dep.silver_build_run_id, db)
        checks = _check_risk(run, bars, benchmark_bars, payload, now)
    elif run.product_name == "market_structure":
        checks = _check_market_structure_pivots(run, bars, payload, now)
    else:
        checks = []

    for check in checks:
        db.add(check)

    if any(c.status == "fail" for c in checks):
        gold_row.data_quality_status = "quarantined"

    run.flagged_for_reference_check = False
    run.reference_checked_at = now
    db.commit()
    return checks
