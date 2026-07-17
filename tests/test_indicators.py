"""Tests for the real technical-indicator engine. All synthetic data, no network."""
import datetime as dt

from catalystiq.analysis.indicators import compute_technical_snapshot
from catalystiq.schemas.market_data import OHLCVBar


def make_bar(date: dt.date, close: float, open_: float | None = None, volume: int = 1_000_000) -> OHLCVBar:
    open_ = close if open_ is None else open_
    return OHLCVBar(
        date=date,
        open=open_,
        high=max(open_, close) + 0.5,
        low=min(open_, close) - 0.5,
        close=close,
        volume=volume,
    )


def business_days(start: dt.date, n: int) -> list[dt.date]:
    days = []
    d = start
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d)
        d += dt.timedelta(days=1)
    return days


def _reading(snapshot, name: str):
    return next(i for i in snapshot.indicators if i.name == name)


def test_no_history_returns_empty_snapshot_with_warning():
    snap = compute_technical_snapshot("EMPTY", [])

    assert snap.bars_used == 0
    assert snap.indicators == []
    assert snap.warnings == ["No price history available."]


def test_short_history_marks_everything_but_obv_insufficient():
    days = business_days(dt.date(2024, 1, 2), 5)
    bars = [make_bar(d, 100 + i) for i, d in enumerate(days)]

    snap = compute_technical_snapshot("short", bars)

    assert snap.symbol == "SHORT"
    for reading in snap.indicators:
        if reading.name == "obv":
            assert reading.status == "computed"
            assert reading.value is not None
            continue
        assert reading.status == "insufficient_data"
        assert reading.value is None
        assert reading.percentile_5y is None
        assert reading.zscore_5y is None


def test_rsi_of_strictly_increasing_series_is_100():
    days = business_days(dt.date(2020, 1, 1), 60)
    bars = [make_bar(d, 100 + i * 0.5) for i, d in enumerate(days)]

    snap = compute_technical_snapshot("UP", bars)

    rsi = _reading(snap, "rsi_14")
    assert rsi.status == "computed"
    assert rsi.value == 100.0


def test_rsi_of_strictly_decreasing_series_is_0():
    days = business_days(dt.date(2020, 1, 1), 60)
    bars = [make_bar(d, 200 - i * 0.5) for i, d in enumerate(days)]

    snap = compute_technical_snapshot("DOWN", bars)

    rsi = _reading(snap, "rsi_14")
    assert rsi.status == "computed"
    assert rsi.value == 0.0


def test_sma_20_matches_hand_computed_average():
    days = business_days(dt.date(2020, 1, 1), 25)
    closes = [100 + i for i in range(len(days))]
    bars = [make_bar(d, c) for d, c in zip(days, closes)]

    snap = compute_technical_snapshot("SMA", bars)

    sma20 = _reading(snap, "sma_20")
    assert sma20.status == "computed"
    expected = sum(closes[-20:]) / 20
    assert abs(sma20.value - expected) < 1e-9


def test_percentile_and_zscore_omitted_below_three_year_history():
    days = business_days(dt.date(2023, 1, 2), 400)  # ~1.5 years
    bars = [make_bar(d, 100 + (i % 5)) for i, d in enumerate(days)]

    snap = compute_technical_snapshot("SHORTHIST", bars)

    assert any("3-year minimum" in w for w in snap.warnings)
    rsi = _reading(snap, "rsi_14")
    assert rsi.status == "computed"
    assert rsi.percentile_5y is None
    assert rsi.zscore_5y is None


def test_percentile_and_zscore_populate_above_three_year_history():
    days = business_days(dt.date(2018, 1, 1), 1400)  # ~5.5 years
    bars = [make_bar(d, 100 + (i % 7) * 0.5) for i, d in enumerate(days)]

    snap = compute_technical_snapshot("LONGHIST", bars)

    assert snap.warnings == []
    rsi = _reading(snap, "rsi_14")
    assert rsi.status == "computed"
    assert rsi.percentile_5y is not None
    assert 0.0 <= rsi.percentile_5y <= 100.0


def test_obv_direction_matches_close_moves():
    days = business_days(dt.date(2024, 1, 2), 3)
    bars = [
        make_bar(days[0], 100, volume=1000),
        make_bar(days[1], 105, volume=500),  # up day: +500
        make_bar(days[2], 102, volume=300),  # down day: -300
    ]

    snap = compute_technical_snapshot("OBV", bars)

    obv = _reading(snap, "obv")
    assert obv.status == "computed"
    assert obv.value == 1200.0  # 1000 (first bar's volume is the OBV base) + 500 - 300
