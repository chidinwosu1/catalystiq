"""Tests for the Market & Sector Context data product (§14.1). All synthetic data, no network."""
import datetime as dt

from catalystiq.analysis.market_context import compute_market_context_snapshot
from catalystiq.schemas.market_data import OHLCVBar


def make_bar(date: dt.date, close: float, volume: int = 1_000_000) -> OHLCVBar:
    return OHLCVBar(date=date, open=close, high=close + 0.5, low=close - 0.5, close=close, volume=volume)


def business_days(start: dt.date, n: int) -> list[dt.date]:
    days = []
    d = start
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d)
        d += dt.timedelta(days=1)
    return days


def _metric(snap, name):
    return next(m for m in snap.metrics if m.name == name)


def test_empty_history_returns_no_metrics():
    snap = compute_market_context_snapshot("EMPTY", [])

    assert snap.metrics == []
    assert "Not enough bars" in snap.warnings[0]


def test_no_market_or_sector_marks_everything_not_supported():
    days = business_days(dt.date(2020, 1, 1), 100)
    bars = [make_bar(d, 100 + i) for i, d in enumerate(days)]

    snap = compute_market_context_snapshot("SOLO", bars)

    assert _metric(snap, "relative_return_20d_vs_market").status == "not_supported"
    assert _metric(snap, "beta_vs_market").status == "not_supported"
    assert _metric(snap, "relative_return_20d_vs_sector").status == "not_supported"
    assert _metric(snap, "sector_leading_or_lagging_market").status == "not_supported"


def test_outperforming_symbol_shows_positive_relative_return_and_leading():
    days = business_days(dt.date(2020, 1, 1), 300)
    sym_bars = [make_bar(d, 100 + i * 0.5) for i, d in enumerate(days)]
    market_bars = [make_bar(d, 300 + i * 0.1) for i, d in enumerate(days)]

    snap = compute_market_context_snapshot("OUT", sym_bars, market_bars=market_bars, market_symbol="SPY")

    assert snap.market_symbol == "SPY"
    assert _metric(snap, "relative_return_20d_vs_market").value > 0
    assert _metric(snap, "relative_return_252d_vs_market").value > 0
    assert _metric(snap, "leading_or_lagging_vs_market").value == "leading"
    assert _metric(snap, "relative_strength_trend_vs_market").value == "rising"


def test_underperforming_symbol_shows_negative_relative_return_and_lagging():
    days = business_days(dt.date(2020, 1, 1), 300)
    sym_bars = [make_bar(d, 300 - i * 0.3) for i, d in enumerate(days)]
    market_bars = [make_bar(d, 300 + i * 0.1) for i, d in enumerate(days)]

    snap = compute_market_context_snapshot("UNDER", sym_bars, market_bars=market_bars, market_symbol="SPY")

    assert _metric(snap, "relative_return_20d_vs_market").value < 0
    assert _metric(snap, "leading_or_lagging_vs_market").value == "lagging"
    assert _metric(snap, "relative_strength_trend_vs_market").value == "falling"


def test_beta_and_correlation_computed_with_enough_aligned_bars():
    days = business_days(dt.date(2020, 1, 1), 300)
    sym_bars = [make_bar(d, 100 + i * 0.5 + (i % 3)) for i, d in enumerate(days)]
    market_bars = [make_bar(d, 300 + i * 0.4 + (i % 5)) for i, d in enumerate(days)]

    snap = compute_market_context_snapshot("BETA", sym_bars, market_bars=market_bars, market_symbol="SPY")

    beta = _metric(snap, "beta_vs_market")
    corr = _metric(snap, "correlation_vs_market")
    assert beta.status == "available"
    assert corr.status == "available"
    assert -1.0 <= corr.value <= 1.0


def test_sector_leading_lagging_market_when_both_provided():
    days = business_days(dt.date(2020, 1, 1), 300)
    sym_bars = [make_bar(d, 100 + i * 0.5) for i, d in enumerate(days)]
    market_bars = [make_bar(d, 300 + i * 0.1) for i, d in enumerate(days)]
    sector_bars = [make_bar(d, 200 + i * 0.4) for i, d in enumerate(days)]  # sector outpaces market

    snap = compute_market_context_snapshot(
        "SEC", sym_bars, market_bars=market_bars, market_symbol="SPY", sector_bars=sector_bars, sector_symbol="XLK"
    )

    assert snap.sector_symbol == "XLK"
    assert _metric(snap, "sector_leading_or_lagging_market").value == "leading"
    assert _metric(snap, "relative_return_20d_vs_sector").status == "available"


def test_mismatched_calendars_align_by_date():
    days = business_days(dt.date(2020, 1, 1), 300)
    sym_bars = [make_bar(d, 100 + i * 0.5) for i, d in enumerate(days)]
    # Benchmark missing every 10th day - alignment should still work via inner join.
    market_bars = [make_bar(d, 300 + i * 0.1) for i, d in enumerate(days) if i % 10 != 0]

    snap = compute_market_context_snapshot("MISALIGNED", sym_bars, market_bars=market_bars, market_symbol="SPY")

    assert _metric(snap, "relative_return_20d_vs_market").status == "available"


def test_symbol_is_uppercased():
    days = business_days(dt.date(2020, 1, 1), 100)
    bars = [make_bar(d, 100 + i) for i, d in enumerate(days)]

    snap = compute_market_context_snapshot("aapl", bars)

    assert snap.symbol == "AAPL"
