"""Volatility & Risk data product (§7 of the quantitative-scoring spec).

Measures observable risk conditions from real OHLCV data - this is
deliberately separate from predicting whether a specific trade will be
profitable (that's the still-out-of-scope Profitability/Opportunity score,
§18). Every metric below is a documented formula; risk-free-rate-dependent
ratios (Sharpe/Sortino/Calmar) use a static, clearly-labeled assumed rate
(`SHARPE_RISK_FREE_RATE_ANNUAL`) since there is no live risk-free-rate
provider yet (deferred to the Macro product, §10) - this is a documented
configuration default, not a fabricated market value.
"""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

from catalystiq.analysis.common import (
    bars_to_frame,
    history_days_available,
    historical_percentile_zscore,
    insufficient,
    make_reading,
)
from catalystiq.schemas.analysis import FeatureReading
from catalystiq.schemas.market_data import OHLCVBar
from catalystiq.schemas.risk import RiskFlag, RiskSnapshot

# --- Configuration (documented, to be promoted to versioned config per §25) ---
REALIZED_VOL_WINDOWS = (10, 20, 60, 252)
DOWNSIDE_DEV_WINDOW = 60
AVG_DAILY_RANGE_WINDOW = 20
GAP_STDEV_WINDOW = 60
VAR_SAMPLE_MAX = 252
VAR_CONFIDENCE = 0.95
CORRELATION_WINDOW = 60
TRADING_DAYS_PER_YEAR = 252
SHARPE_RISK_FREE_RATE_ANNUAL = 0.0  # static documented assumption, not a live source

MIN_BARS_FOR_VAR = 60
MIN_BARS_FOR_RATIOS = 61

ELEVATED_VOL_PERCENTILE_THRESHOLD = 80
EXTREME_ATR_PERCENTILE_THRESHOLD = 90
LARGE_GAP_STDEV_THRESHOLD_PCT = 2.0
HIGH_CORRELATION_THRESHOLD = 0.8
SIGNIFICANT_DRAWDOWN_THRESHOLD_PCT = -10.0
THIN_LIQUIDITY_DOLLAR_VOLUME_THRESHOLD = 1_000_000.0


def _log_returns(closes: pd.Series) -> pd.Series:
    return np.log(closes / closes.shift(1))


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


def _atr_series(df: pd.DataFrame, period: int = 14) -> pd.Series:
    return _true_range(df).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def _drawdown_series(closes: pd.Series) -> pd.Series:
    rolling_peak = closes.cummax()
    return (closes - rolling_peak) / rolling_peak * 100


def compute_risk_snapshot(
    symbol: str,
    bars: list[OHLCVBar],
    benchmark_bars: list[OHLCVBar] | None = None,
    benchmark_symbol: str | None = None,
) -> RiskSnapshot:
    bars_used = len(bars)
    days_available = history_days_available(bars)
    now = dt.datetime.now(dt.timezone.utc)

    if bars_used < 15:
        return RiskSnapshot(
            symbol=symbol.upper(),
            benchmark_symbol=benchmark_symbol,
            as_of=now,
            bars_used=bars_used,
            history_days_available=days_available,
            metrics=[],
            flags=[],
            warnings=["Not enough bars to compute any risk metrics."],
        )

    df = bars_to_frame(bars)
    closes = df["close"]
    returns = _log_returns(closes)
    atr = _atr_series(df, 14)
    atr_pct = atr / closes * 100
    drawdown = _drawdown_series(closes)

    metrics: list[FeatureReading] = []

    metrics.append(make_reading("atr_14", round(float(atr.iloc[-1]), 4), "14-period average true range, price units.", {"period": 14}))
    metrics.append(make_reading("atr_14_pct", round(float(atr_pct.iloc[-1]), 4), "14-period ATR as percent of close.", {"period": 14}))

    atr_pctile, atr_zscore = historical_percentile_zscore(atr_pct, days_available)

    for window in REALIZED_VOL_WINDOWS:
        name = f"realized_volatility_{window}d_annualized_pct"
        if bars_used < window + 1:
            metrics.append(insufficient(name, f"Annualized stdev of {window}-day daily log returns.", {"window": window}))
            continue
        vol = float(returns.tail(window).std() * (TRADING_DAYS_PER_YEAR**0.5) * 100)
        vol_series = returns.rolling(window=window, min_periods=window).std() * (TRADING_DAYS_PER_YEAR**0.5) * 100
        pctile, zscore = historical_percentile_zscore(vol_series, days_available)
        metrics.append(make_reading(name, round(vol, 4), f"Annualized stdev of {window}-day daily log returns.", {"window": window}, percentile_5y=pctile, zscore_5y=zscore))

    if bars_used >= DOWNSIDE_DEV_WINDOW + 1:
        recent_returns = returns.tail(DOWNSIDE_DEV_WINDOW)
        downside = recent_returns[recent_returns < 0]
        downside_dev = float(downside.std() * (TRADING_DAYS_PER_YEAR**0.5) * 100) if len(downside) >= 2 else 0.0
        metrics.append(make_reading("downside_deviation_annualized_pct", round(downside_dev, 4), f"Annualized stdev of negative daily returns over {DOWNSIDE_DEV_WINDOW} sessions.", {"window": DOWNSIDE_DEV_WINDOW}))
    else:
        downside_dev = None
        metrics.append(insufficient("downside_deviation_annualized_pct", f"Annualized stdev of negative daily returns over {DOWNSIDE_DEV_WINDOW} sessions.", {"window": DOWNSIDE_DEV_WINDOW}))

    max_dd = float(drawdown.min())
    current_dd = float(drawdown.iloc[-1])
    metrics.append(make_reading("max_drawdown_pct", round(max_dd, 4), "Maximum peak-to-trough decline over the available history.", {}))
    metrics.append(make_reading("current_drawdown_pct", round(current_dd, 4), "Current decline from the rolling peak.", {}))

    if bars_used >= AVG_DAILY_RANGE_WINDOW:
        adr = float(((df["high"] - df["low"]) / df["close"] * 100).tail(AVG_DAILY_RANGE_WINDOW).mean())
        metrics.append(make_reading("average_daily_range_pct", round(adr, 4), f"Average of (high-low)/close over the last {AVG_DAILY_RANGE_WINDOW} sessions.", {"window": AVG_DAILY_RANGE_WINDOW}))
    else:
        metrics.append(insufficient("average_daily_range_pct", f"Average of (high-low)/close over the last {AVG_DAILY_RANGE_WINDOW} sessions.", {"window": AVG_DAILY_RANGE_WINDOW}))

    if bars_used >= 3:
        prior_close = df["close"].shift(1)
        gap_pct = (df["open"] - prior_close) / prior_close * 100
        gap_window = gap_pct.dropna().tail(GAP_STDEV_WINDOW)
        gap_stdev = float(gap_window.std()) if len(gap_window) >= 2 else 0.0
        metrics.append(make_reading("gap_stdev_pct", round(gap_stdev, 4), f"Stdev of open-vs-prior-close gaps over the last {min(GAP_STDEV_WINDOW, len(gap_window))} sessions.", {"window": GAP_STDEV_WINDOW}))
    else:
        gap_stdev = None
        metrics.append(insufficient("gap_stdev_pct", f"Stdev of open-vs-prior-close gaps over the last {GAP_STDEV_WINDOW} sessions.", {"window": GAP_STDEV_WINDOW}))

    session_returns_pct = (returns.dropna() * 100)
    if len(session_returns_pct) >= 2:
        metrics.append(make_reading("worst_session_return_pct", round(float(session_returns_pct.min()), 4), "Worst single-session return over the available history.", {}))
        metrics.append(make_reading("best_session_return_pct", round(float(session_returns_pct.max()), 4), "Best single-session return over the available history.", {}))
    else:
        metrics.append(insufficient("worst_session_return_pct", "Worst single-session return over the available history.", {}))
        metrics.append(insufficient("best_session_return_pct", "Best single-session return over the available history.", {}))

    var_sample = session_returns_pct.tail(VAR_SAMPLE_MAX)
    if len(var_sample) >= MIN_BARS_FOR_VAR:
        hist_var = float(-np.percentile(var_sample, (1 - VAR_CONFIDENCE) * 100))
        tail_losses = var_sample[var_sample <= -hist_var]
        cvar = float(-tail_losses.mean()) if len(tail_losses) > 0 else hist_var
        param_var = float(-(var_sample.mean() + var_sample.std() * -1.6448536269514722))  # 95% one-tailed z
        metrics.append(make_reading("historical_var_95_pct", round(hist_var, 4), "Historical 95% Value-at-Risk (single-session loss), percent.", {"confidence": VAR_CONFIDENCE, "sample_size": len(var_sample)}))
        metrics.append(make_reading("parametric_var_95_pct", round(param_var, 4), "Parametric (normal-distribution) 95% Value-at-Risk, percent.", {"confidence": VAR_CONFIDENCE, "sample_size": len(var_sample)}))
        metrics.append(make_reading("cvar_95_pct", round(cvar, 4), "Conditional VaR (expected loss beyond the 95% VaR threshold), percent.", {"confidence": VAR_CONFIDENCE, "sample_size": len(var_sample)}))
    else:
        hist_var = cvar = None
        metrics.append(insufficient("historical_var_95_pct", "Historical 95% Value-at-Risk, percent.", {"confidence": VAR_CONFIDENCE}))
        metrics.append(insufficient("parametric_var_95_pct", "Parametric 95% Value-at-Risk, percent.", {"confidence": VAR_CONFIDENCE}))
        metrics.append(insufficient("cvar_95_pct", "Conditional VaR, percent.", {"confidence": VAR_CONFIDENCE}))

    beta = correlation = None
    if benchmark_bars:
        bench_df = bars_to_frame(benchmark_bars)
        bench_returns = _log_returns(bench_df["close"])
        aligned = pd.DataFrame({"asset": returns, "benchmark": bench_returns}).dropna()
        if len(aligned) >= MIN_BARS_FOR_RATIOS:
            cov = aligned["asset"].cov(aligned["benchmark"])
            bench_var = aligned["benchmark"].var()
            beta = float(cov / bench_var) if bench_var > 0 else None
            corr_window = aligned.tail(CORRELATION_WINDOW)
            correlation = float(corr_window["asset"].corr(corr_window["benchmark"])) if len(corr_window) >= 2 else None

            metrics.append(
                make_reading("beta_vs_benchmark", round(beta, 4) if beta is not None else None, f"Beta versus {benchmark_symbol or 'benchmark'}.", {"benchmark": benchmark_symbol or ""}, status="available" if beta is not None else "invalid")
            )
            metrics.append(
                make_reading(f"rolling_correlation_{CORRELATION_WINDOW}d_vs_benchmark", round(correlation, 4) if correlation is not None else None, f"Rolling {CORRELATION_WINDOW}-day correlation versus {benchmark_symbol or 'benchmark'}.", {"window": CORRELATION_WINDOW, "benchmark": benchmark_symbol or ""}, status="available" if correlation is not None else "invalid")
            )
        else:
            metrics.append(insufficient("beta_vs_benchmark", f"Beta versus {benchmark_symbol or 'benchmark'}.", {"benchmark": benchmark_symbol or ""}))
            metrics.append(insufficient(f"rolling_correlation_{CORRELATION_WINDOW}d_vs_benchmark", f"Rolling {CORRELATION_WINDOW}-day correlation versus {benchmark_symbol or 'benchmark'}.", {"window": CORRELATION_WINDOW}))
    else:
        metrics.append(make_reading("beta_vs_benchmark", None, "Beta versus a benchmark.", {}, status="not_supported"))
        metrics.append(make_reading(f"rolling_correlation_{CORRELATION_WINDOW}d_vs_benchmark", None, f"Rolling {CORRELATION_WINDOW}-day correlation versus a benchmark.", {"window": CORRELATION_WINDOW}, status="not_supported"))

    if bars_used >= MIN_BARS_FOR_RATIOS and downside_dev is not None:
        window_returns = returns.tail(DOWNSIDE_DEV_WINDOW)
        mean_annual_return = float(window_returns.mean() * TRADING_DAYS_PER_YEAR * 100)
        annual_vol = float(window_returns.std() * (TRADING_DAYS_PER_YEAR**0.5) * 100)
        sharpe = (mean_annual_return - SHARPE_RISK_FREE_RATE_ANNUAL * 100) / annual_vol if annual_vol > 0 else None
        sortino = (mean_annual_return - SHARPE_RISK_FREE_RATE_ANNUAL * 100) / downside_dev if downside_dev > 0 else None
        calmar = mean_annual_return / abs(max_dd) if max_dd < 0 else None

        params = {"risk_free_rate_assumed_annual_pct": SHARPE_RISK_FREE_RATE_ANNUAL * 100, "window": DOWNSIDE_DEV_WINDOW}
        metrics.append(make_reading("sharpe_ratio", round(sharpe, 4) if sharpe is not None else None, "Annualized return minus assumed risk-free rate, divided by annualized volatility.", params, status="available" if sharpe is not None else "invalid"))
        metrics.append(make_reading("sortino_ratio", round(sortino, 4) if sortino is not None else None, "Annualized return minus assumed risk-free rate, divided by downside deviation.", params, status="available" if sortino is not None else "invalid"))
        metrics.append(make_reading("calmar_ratio", round(calmar, 4) if calmar is not None else None, "Annualized return divided by absolute max drawdown.", {"window": DOWNSIDE_DEV_WINDOW}, status="available" if calmar is not None else "invalid"))
    else:
        metrics.append(insufficient("sharpe_ratio", "Annualized return minus assumed risk-free rate, divided by annualized volatility.", {}))
        metrics.append(insufficient("sortino_ratio", "Annualized return minus assumed risk-free rate, divided by downside deviation.", {}))
        metrics.append(insufficient("calmar_ratio", "Annualized return divided by absolute max drawdown.", {}))

    flags = _risk_flags(
        atr_pctile=atr_pctile,
        current_drawdown=current_dd,
        gap_stdev=gap_stdev,
        correlation=correlation,
        avg_dollar_volume=float((df["close"] * df["volume"]).tail(20).median()) if bars_used >= 20 else None,
        now=now,
    )

    warnings: list[str] = []
    if days_available < 3 * 365:
        warnings.append("Under three years of history - realized-volatility percentile/z-score omitted where noted.")

    return RiskSnapshot(
        symbol=symbol.upper(),
        benchmark_symbol=benchmark_symbol,
        as_of=now,
        bars_used=bars_used,
        history_days_available=days_available,
        metrics=metrics,
        flags=flags,
        warnings=warnings,
    )


def _risk_flags(
    *,
    atr_pctile: float | None,
    current_drawdown: float,
    gap_stdev: float | None,
    correlation: float | None,
    avg_dollar_volume: float | None,
    now: dt.datetime,
) -> list[RiskFlag]:
    flags: list[RiskFlag] = []

    if atr_pctile is not None and atr_pctile >= EXTREME_ATR_PERCENTILE_THRESHOLD:
        flags.append(
            RiskFlag(
                flag="extreme_atr_percentile",
                severity="high",
                triggering_value=round(atr_pctile, 2),
                threshold=EXTREME_ATR_PERCENTILE_THRESHOLD,
                explanation=f"ATR% is at the {atr_pctile:.0f}th percentile of its own multi-year history.",
                source_timestamp=now,
            )
        )

    if current_drawdown <= SIGNIFICANT_DRAWDOWN_THRESHOLD_PCT:
        severity = "high" if current_drawdown <= 2 * SIGNIFICANT_DRAWDOWN_THRESHOLD_PCT else "moderate"
        flags.append(
            RiskFlag(
                flag="significant_recent_drawdown",
                severity=severity,
                triggering_value=round(current_drawdown, 2),
                threshold=SIGNIFICANT_DRAWDOWN_THRESHOLD_PCT,
                explanation=f"Currently {current_drawdown:.1f}% below the rolling peak.",
                source_timestamp=now,
            )
        )

    if gap_stdev is not None and gap_stdev >= LARGE_GAP_STDEV_THRESHOLD_PCT:
        flags.append(
            RiskFlag(
                flag="large_overnight_gap_behavior",
                severity="moderate",
                triggering_value=round(gap_stdev, 2),
                threshold=LARGE_GAP_STDEV_THRESHOLD_PCT,
                explanation=f"Open-vs-prior-close gap standard deviation is {gap_stdev:.2f}%.",
                source_timestamp=now,
            )
        )

    if correlation is not None and abs(correlation) >= HIGH_CORRELATION_THRESHOLD:
        flags.append(
            RiskFlag(
                flag="high_benchmark_correlation",
                severity="low",
                triggering_value=round(correlation, 2),
                threshold=HIGH_CORRELATION_THRESHOLD,
                explanation=f"Rolling correlation to the benchmark is {correlation:.2f}, offering limited diversification.",
                source_timestamp=now,
            )
        )

    if avg_dollar_volume is not None and avg_dollar_volume < THIN_LIQUIDITY_DOLLAR_VOLUME_THRESHOLD:
        flags.append(
            RiskFlag(
                flag="thin_liquidity",
                severity="high",
                triggering_value=round(avg_dollar_volume, 0),
                threshold=THIN_LIQUIDITY_DOLLAR_VOLUME_THRESHOLD,
                explanation=f"Median daily dollar volume (${avg_dollar_volume:,.0f}) is below the configured liquidity threshold.",
                source_timestamp=now,
            )
        )

    return flags
