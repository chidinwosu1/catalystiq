"""Tests for the Volume & Liquidity data product (§8). All synthetic data, no network."""
import datetime as dt
import random

from catalystiq.analysis.volume_liquidity import compute_volume_liquidity_snapshot
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


def random_walk_bars(n: int, seed: int = 1, volume: int = 1_000_000) -> list[OHLCVBar]:
    rng = random.Random(seed)
    days = business_days(dt.date(2020, 1, 1), n)
    price = 100.0
    bars = []
    for d in days:
        open_ = price
        price *= 1 + rng.uniform(-0.02, 0.021)
        bars.append(make_bar(d, round(price, 2), open_=round(open_, 2), volume=volume + rng.randint(-int(volume * 0.2), int(volume * 0.2))))
    return bars


def _metric(snap, name):
    return next(m for m in snap.metrics if m.name == name)


def test_empty_history_returns_no_metrics():
    snap = compute_volume_liquidity_snapshot("EMPTY", [])

    assert snap.metrics == []
    assert snap.liquidity_classification.status == "insufficient_data"


def test_all_windows_computed_with_enough_bars():
    bars = random_walk_bars(250)

    snap = compute_volume_liquidity_snapshot("LONG", bars)

    for window in (5, 20, 60, 200):
        m = _metric(snap, f"average_daily_volume_{window}d")
        assert m.status == "available"
        assert m.value > 0


def test_strong_up_closes_produce_positive_adl_and_cmf():
    days = business_days(dt.date(2020, 1, 1), 60)
    bars = [make_bar(d, 100 + i, open_=100 + i - 3, volume=1_000_000) for i, d in enumerate(days)]

    snap = compute_volume_liquidity_snapshot("UPCLOSE", bars)

    assert _metric(snap, "accumulation_distribution_line").value > 0
    assert _metric(snap, "chaikin_money_flow").value > 0


def test_strong_down_closes_produce_negative_adl_and_cmf():
    days = business_days(dt.date(2020, 1, 1), 60)
    bars = [make_bar(d, 100 - i, open_=100 - i + 3, volume=1_000_000) for i, d in enumerate(days)]

    snap = compute_volume_liquidity_snapshot("DOWNCLOSE", bars)

    assert _metric(snap, "accumulation_distribution_line").value < 0
    assert _metric(snap, "chaikin_money_flow").value < 0


def test_liquidity_classification_high_for_large_dollar_volume():
    bars = random_walk_bars(60, volume=5_000_000)  # ~$500M/day dollar volume at price ~100

    snap = compute_volume_liquidity_snapshot("BIG", bars)

    assert snap.liquidity_classification.value == "high"


def test_liquidity_classification_very_low_for_penny_thin_stock():
    days = business_days(dt.date(2020, 1, 1), 60)
    bars = [make_bar(d, 2.0, volume=5000) for d in days]

    snap = compute_volume_liquidity_snapshot("PENNY", bars)

    assert snap.liquidity_classification.value == "very_low"


def test_turnover_ratio_not_supported_without_shares_outstanding():
    bars = random_walk_bars(60)

    snap = compute_volume_liquidity_snapshot("NOTURN", bars)

    assert _metric(snap, "turnover_ratio_pct").status == "not_supported"


def test_turnover_ratio_computed_when_shares_outstanding_provided():
    bars = random_walk_bars(60)

    snap = compute_volume_liquidity_snapshot("TURN", bars, shares_outstanding=100_000_000)

    m = _metric(snap, "turnover_ratio_pct")
    assert m.status == "available"
    assert m.value > 0


def test_bid_ask_metrics_always_not_supported():
    bars = random_walk_bars(60)

    snap = compute_volume_liquidity_snapshot("SPREAD", bars)

    assert _metric(snap, "bid_ask_spread").status == "not_supported"
    assert _metric(snap, "spread_pct_of_mid").status == "not_supported"
    assert _metric(snap, "estimated_slippage_band").status == "not_supported"


def test_symbol_is_uppercased():
    bars = random_walk_bars(60)

    snap = compute_volume_liquidity_snapshot("aapl", bars)

    assert snap.symbol == "AAPL"
