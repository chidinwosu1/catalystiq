"""Tests for the Volatility & Risk data product (§7). All synthetic data, no network."""
import datetime as dt
import random

from catalystiq.analysis.risk import compute_risk_snapshot
from catalystiq.schemas.market_data import OHLCVBar


def make_bar(date: dt.date, close: float, open_: float | None = None, volume: int = 1_000_000) -> OHLCVBar:
    open_ = close if open_ is None else open_
    return OHLCVBar(date=date, open=open_, high=max(open_, close) + 0.5, low=min(open_, close) - 0.5, close=close, volume=volume)


def business_days(start: dt.date, n: int) -> list[dt.date]:
    days = []
    d = start
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d)
        d += dt.timedelta(days=1)
    return days


def random_walk_bars(n: int, seed: int = 1, start_price: float = 100.0, volume: int = 1_000_000) -> list[OHLCVBar]:
    rng = random.Random(seed)
    days = business_days(dt.date(2019, 1, 1), n)
    price = start_price
    bars = []
    for d in days:
        price *= 1 + rng.uniform(-0.02, 0.021)
        bars.append(make_bar(d, round(price, 2), volume=volume + rng.randint(-int(volume * 0.2), int(volume * 0.2))))
    return bars


def _metric(snap, name):
    return next(m for m in snap.metrics if m.name == name)


def test_empty_history_returns_no_metrics():
    snap = compute_risk_snapshot("EMPTY", [])

    assert snap.metrics == []
    assert snap.flags == []
    assert "Not enough bars" in snap.warnings[0]


def test_short_history_marks_var_and_ratios_insufficient():
    bars = random_walk_bars(30)

    snap = compute_risk_snapshot("SHORT", bars)

    assert _metric(snap, "historical_var_95_pct").status == "insufficient_data"
    assert _metric(snap, "sharpe_ratio").status == "insufficient_data"
    assert _metric(snap, "atr_14").status == "available"  # only needs 14 bars


def test_realized_volatility_windows_all_computed_with_enough_bars():
    bars = random_walk_bars(400)

    snap = compute_risk_snapshot("LONG", bars)

    for window in (10, 20, 60, 252):
        m = _metric(snap, f"realized_volatility_{window}d_annualized_pct")
        assert m.status == "available"
        assert m.value > 0  # a real random walk has nonzero volatility


def test_max_drawdown_is_negative_or_zero():
    bars = random_walk_bars(400)

    snap = compute_risk_snapshot("DD", bars)

    assert _metric(snap, "max_drawdown_pct").value <= 0
    assert _metric(snap, "current_drawdown_pct").value <= 0


def test_significant_drawdown_flag_fires_on_a_real_crash():
    days = business_days(dt.date(2020, 1, 1), 100)
    bars = [make_bar(d, 100) for d in days[:60]]
    # A sharp, sustained 20% decline over the remaining bars.
    for i, d in enumerate(days[60:]):
        bars.append(make_bar(d, 100 * (1 - 0.20 * (i + 1) / 40)))

    snap = compute_risk_snapshot("CRASH", bars)

    flags = {f.flag: f for f in snap.flags}
    assert "significant_recent_drawdown" in flags
    assert flags["significant_recent_drawdown"].severity in ("moderate", "high")


def test_thin_liquidity_flag_fires_for_low_dollar_volume():
    bars = random_walk_bars(60, volume=100, start_price=1.0)

    snap = compute_risk_snapshot("THIN", bars)

    flags = {f.flag for f in snap.flags}
    assert "thin_liquidity" in flags


def test_no_benchmark_marks_beta_and_correlation_not_supported():
    bars = random_walk_bars(400)

    snap = compute_risk_snapshot("NOBENCH", bars)

    assert _metric(snap, "beta_vs_benchmark").status == "not_supported"
    assert _metric(snap, "rolling_correlation_60d_vs_benchmark").status == "not_supported"


def test_benchmark_provided_computes_beta_and_correlation():
    bars = random_walk_bars(400, seed=1)
    benchmark_bars = random_walk_bars(400, seed=2, start_price=300.0)

    snap = compute_risk_snapshot("WITHBENCH", bars, benchmark_bars=benchmark_bars, benchmark_symbol="SPY")

    assert snap.benchmark_symbol == "SPY"
    assert _metric(snap, "beta_vs_benchmark").status == "available"
    assert _metric(snap, "rolling_correlation_60d_vs_benchmark").status == "available"
    corr = _metric(snap, "rolling_correlation_60d_vs_benchmark").value
    assert -1.0 <= corr <= 1.0


def test_sharpe_sortino_calmar_computed_with_enough_bars():
    bars = random_walk_bars(400)

    snap = compute_risk_snapshot("RATIOS", bars)

    assert _metric(snap, "sharpe_ratio").status == "available"
    assert _metric(snap, "sortino_ratio").status == "available"
    assert _metric(snap, "calmar_ratio").status == "available"
    assert _metric(snap, "sharpe_ratio").params["risk_free_rate_assumed_annual_pct"] == 0.0


def test_symbol_is_uppercased():
    bars = random_walk_bars(400)

    snap = compute_risk_snapshot("aapl", bars)

    assert snap.symbol == "AAPL"
