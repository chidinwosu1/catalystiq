"""Hand-computed checks for the independent financial-statistics
reference implementations (catalystiq/validation/reference/
independent_stats.py) - not circular against Catalyst IQ's own code."""
import numpy as np

from catalystiq.validation.reference import independent_stats as stats


def test_beta_is_one_when_asset_mirrors_benchmark():
    benchmark = np.array([100.0 * (1.01**i) for i in range(60)])
    asset = benchmark.copy()  # identical log returns -> beta == 1
    r = stats.beta(asset, benchmark)
    assert abs(r.value - 1.0) < 1e-9


def test_beta_is_two_when_asset_moves_double_the_benchmark():
    rng = np.random.RandomState(1)
    bench_returns = rng.normal(0, 0.01, 100)
    bench_close = 100.0 * np.exp(np.cumsum(bench_returns))
    asset_close = 100.0 * np.exp(np.cumsum(bench_returns * 2))
    r = stats.beta(asset_close, bench_close)
    assert abs(r.value - 2.0) < 1e-6


def test_sharpe_is_none_for_exactly_zero_volatility():
    close = np.array([100.0] * 70)  # flat -> log returns are exactly 0.0
    r = stats.sharpe_ratio(close, window=60, risk_free_rate_annual=0.0, trading_days_per_year=252)
    assert r.value is None


def test_historical_var_hand_computed_percentile():
    # 100 evenly-spaced log returns; 95% historical VaR is the negative of
    # the 5th percentile return.
    close = 100.0 * np.exp(np.cumsum(np.linspace(-0.05, 0.05, 101)))
    r = stats.historical_var(close, sample_max=252, confidence=0.95)
    log_returns = np.diff(np.log(close)) * 100.0
    expected = -float(np.percentile(log_returns, 5))
    assert abs(r.value - expected) < 1e-6


def test_parametric_var_uses_normal_quantile():
    from scipy.stats import norm

    close = 100.0 * np.exp(np.cumsum(np.random.RandomState(2).normal(0, 0.01, 300)))
    r = stats.parametric_var(close, sample_max=252, confidence=0.95)
    log_returns = np.diff(np.log(close))[-252:] * 100.0
    z = norm.ppf(0.05)
    expected = -(np.mean(log_returns) + np.std(log_returns, ddof=1) * z)
    assert abs(r.value - expected) < 1e-9


def test_calmar_is_none_when_no_drawdown():
    close = np.array([100.0 * (1.001**i) for i in range(70)])  # monotonic up, no drawdown
    r = stats.calmar_ratio(close, window=60, trading_days_per_year=252)
    assert r.value is None


def test_insufficient_bars_returns_none():
    close = np.array([100.0, 101.0])
    assert stats.sharpe_ratio(close, 60, 0.0, 252).value is None
    assert stats.sortino_ratio(close, 60, 0.0, 252).value is None
    assert stats.historical_var(close, 252, 0.95).value is None
    assert stats.parametric_var(close, 252, 0.95).value is None
