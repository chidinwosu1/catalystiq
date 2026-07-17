"""Independent financial-statistics reference implementations for Beta,
Sharpe/Sortino/Calmar, and Value-at-Risk - via numpy/scipy directly rather
than Catalyst IQ's own pandas-based code path
(catalystiq/analysis/risk.py), so this genuinely tests for a coding bug
rather than re-running the same code under a different name.

Uses the SAME conventions risk.py already documents (log returns, a
static assumed risk-free rate, 252-day annualization, a 60-session window
for Sharpe/Sortino, full-history Beta/Calmar) - the point is to catch code
bugs, not to litigate convention choices, so parameters must match exactly
per risk.py's own module docstring before comparing.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.stats import norm


@dataclass(frozen=True)
class ReferenceValue:
    value: float | None
    formula: str


_BETA_FORMULA = "Beta = Cov(asset_log_returns, benchmark_log_returns) / Var(benchmark_log_returns)"


def beta(asset_close: np.ndarray, benchmark_close: np.ndarray) -> ReferenceValue:
    """Beta over the FULL aligned log-return history - matches risk.py's
    own convention (unlike its rolling-window correlation, beta there is
    computed from the whole aligned series, not a trailing window)."""
    n = min(len(asset_close), len(benchmark_close))
    if n < 2:
        return ReferenceValue(None, _BETA_FORMULA)
    asset_returns = np.diff(np.log(asset_close[-n:]))
    bench_returns = np.diff(np.log(benchmark_close[-n:]))
    if len(asset_returns) < 2:
        return ReferenceValue(None, _BETA_FORMULA)
    bench_var = float(np.var(bench_returns, ddof=1))
    if bench_var <= 0:
        return ReferenceValue(None, _BETA_FORMULA)
    cov = float(np.cov(asset_returns, bench_returns, ddof=1)[0, 1])
    return ReferenceValue(cov / bench_var, _BETA_FORMULA)


_SHARPE_FORMULA = "Sharpe = (annualized_mean_return - risk_free_rate) / annualized_volatility"
_SORTINO_FORMULA = "Sortino = (annualized_mean_return - risk_free_rate) / annualized_downside_deviation"
_CALMAR_FORMULA = "Calmar = annualized_mean_return / abs(max_drawdown)"


def _annualized_mean_and_vol(
    log_returns: np.ndarray, window: int, trading_days_per_year: int
) -> tuple[float, float]:
    recent = log_returns[-window:]
    mean_annual_return = float(np.mean(recent)) * trading_days_per_year * 100.0
    annual_vol = float(np.std(recent, ddof=1)) * (trading_days_per_year**0.5) * 100.0
    return mean_annual_return, annual_vol


def sharpe_ratio(
    close: np.ndarray,
    window: int,
    risk_free_rate_annual: float,
    trading_days_per_year: int,
) -> ReferenceValue:
    log_returns = np.diff(np.log(close))
    if len(log_returns) < window + 1:
        return ReferenceValue(None, _SHARPE_FORMULA)
    mean_annual_return, annual_vol = _annualized_mean_and_vol(log_returns, window, trading_days_per_year)
    if annual_vol <= 0:
        return ReferenceValue(None, _SHARPE_FORMULA)
    return ReferenceValue(
        (mean_annual_return - risk_free_rate_annual * 100.0) / annual_vol, _SHARPE_FORMULA
    )


def sortino_ratio(
    close: np.ndarray,
    window: int,
    risk_free_rate_annual: float,
    trading_days_per_year: int,
) -> ReferenceValue:
    log_returns = np.diff(np.log(close))
    if len(log_returns) < window + 1:
        return ReferenceValue(None, _SORTINO_FORMULA)
    recent = log_returns[-window:]
    mean_annual_return = float(np.mean(recent)) * trading_days_per_year * 100.0
    downside = recent[recent < 0]
    if len(downside) < 2:
        return ReferenceValue(None, _SORTINO_FORMULA)
    downside_dev = float(np.std(downside, ddof=1)) * (trading_days_per_year**0.5) * 100.0
    if downside_dev <= 0:
        return ReferenceValue(None, _SORTINO_FORMULA)
    return ReferenceValue(
        (mean_annual_return - risk_free_rate_annual * 100.0) / downside_dev, _SORTINO_FORMULA
    )


def calmar_ratio(
    close: np.ndarray,
    window: int,
    trading_days_per_year: int,
) -> ReferenceValue:
    log_returns = np.diff(np.log(close))
    if len(log_returns) < window + 1:
        return ReferenceValue(None, _CALMAR_FORMULA)
    mean_annual_return = float(np.mean(log_returns[-window:])) * trading_days_per_year * 100.0

    running_peak = np.maximum.accumulate(close)
    drawdown_pct = (close - running_peak) / running_peak * 100.0
    max_dd = float(np.min(drawdown_pct))
    if max_dd >= 0:
        return ReferenceValue(None, _CALMAR_FORMULA)
    return ReferenceValue(mean_annual_return / abs(max_dd), _CALMAR_FORMULA)


_HISTORICAL_VAR_FORMULA = "Historical VaR(confidence) = -percentile(session_returns_pct, (1-confidence)*100)"
_PARAMETRIC_VAR_FORMULA = "Parametric VaR(confidence) = -(mean(returns) + std(returns) * z(1-confidence))"


def historical_var(close: np.ndarray, sample_max: int, confidence: float) -> ReferenceValue:
    log_returns = np.diff(np.log(close)) * 100.0
    sample = log_returns[-sample_max:]
    if len(sample) < 2:
        return ReferenceValue(None, _HISTORICAL_VAR_FORMULA)
    return ReferenceValue(
        -float(np.percentile(sample, (1 - confidence) * 100)), _HISTORICAL_VAR_FORMULA
    )


def parametric_var(close: np.ndarray, sample_max: int, confidence: float) -> ReferenceValue:
    log_returns = np.diff(np.log(close)) * 100.0
    sample = log_returns[-sample_max:]
    if len(sample) < 2:
        return ReferenceValue(None, _PARAMETRIC_VAR_FORMULA)
    z = float(norm.ppf(1 - confidence))
    return ReferenceValue(
        -(float(np.mean(sample)) + float(np.std(sample, ddof=1)) * z), _PARAMETRIC_VAR_FORMULA
    )
