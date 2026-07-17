"""Tests for the Data Validation Layer (§2.9). All synthetic data, no network."""
import datetime as dt

from catalystiq.schemas.market_data import OHLCVBar, Quote
from catalystiq.schemas.validation import DataQualityIssueType
from catalystiq.validation.data_quality import (
    check_chronological_order,
    check_history_depth,
    check_ohlc_relationships,
    cross_check_live_quote,
    dedupe_bars,
    flag_abnormal_gaps,
    flag_missing_trading_days,
    validate_price_history,
)


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


def test_check_chronological_order_flags_out_of_order_rows():
    d1, d2, d3 = dt.date(2024, 1, 2), dt.date(2024, 1, 3), dt.date(2024, 1, 4)
    bars = [make_bar(d1, 100), make_bar(d3, 102), make_bar(d2, 101)]

    issues = check_chronological_order(bars)

    assert len(issues) == 1
    assert issues[0].type == DataQualityIssueType.OUT_OF_ORDER
    assert issues[0].date == d2


def test_check_chronological_order_passes_sorted_input():
    days = business_days(dt.date(2024, 1, 2), 5)
    bars = [make_bar(d, 100 + i) for i, d in enumerate(days)]

    assert check_chronological_order(bars) == []


def test_dedupe_bars_keeps_first_occurrence():
    d = dt.date(2024, 1, 2)
    bars = [make_bar(d, 100), make_bar(d, 999)]

    cleaned, issues = dedupe_bars(bars)

    assert len(cleaned) == 1
    assert cleaned[0].close == 100
    assert len(issues) == 1
    assert issues[0].type == DataQualityIssueType.DUPLICATE_ROW


def test_flag_missing_trading_days_detects_gap_in_range():
    # Jan 2 - Jan 10 2024 is a full NYSE week+ (Jan 15 is MLK holiday, out of range here).
    days = business_days(dt.date(2024, 1, 2), 7)
    days_with_gap = [d for d in days if d != days[3]]  # drop one weekday in the middle
    bars = [make_bar(d, 100) for d in days_with_gap]

    issues = flag_missing_trading_days(bars)

    assert any(i.type == DataQualityIssueType.MISSING_TRADING_DAY and i.date == days[3] for i in issues)


def test_flag_missing_trading_days_no_gaps():
    days = business_days(dt.date(2024, 1, 2), 5)
    bars = [make_bar(d, 100) for d in days]

    assert flag_missing_trading_days(bars) == []


def test_flag_abnormal_gaps_detects_outlier():
    days = business_days(dt.date(2024, 1, 2), 15)
    closes = [100 + (i % 3) * 0.1 for i in range(len(days))]  # tiny, quiet moves
    bars = [make_bar(days[0], closes[0])]
    for i in range(1, len(days)):
        open_ = closes[i]
        if i == 10:
            open_ = bars[-1].close * 1.5  # a 50% overnight gap, way outside the quiet regime
        bars.append(make_bar(days[i], closes[i], open_=open_))

    issues = flag_abnormal_gaps(bars)

    assert any(i.type == DataQualityIssueType.ABNORMAL_GAP and i.date == days[10] for i in issues)


def test_flag_abnormal_gaps_quiet_market_no_flags():
    days = business_days(dt.date(2024, 1, 2), 10)
    bars = [make_bar(d, 100 + (i % 2) * 0.05) for i, d in enumerate(days)]

    assert flag_abnormal_gaps(bars) == []


def test_cross_check_live_quote_flags_mismatch():
    bars = [make_bar(dt.date(2024, 1, 2), 100)]
    quote = Quote(symbol="TEST", price=140, previous_close=140, as_of=dt.datetime.now(dt.timezone.utc))

    issues = cross_check_live_quote(bars, quote)

    assert len(issues) == 1
    assert issues[0].type == DataQualityIssueType.LIVE_QUOTE_MISMATCH


def test_cross_check_live_quote_passes_when_close():
    bars = [make_bar(dt.date(2024, 1, 2), 100)]
    quote = Quote(symbol="TEST", price=100.4, previous_close=100.4, as_of=dt.datetime.now(dt.timezone.utc))

    assert cross_check_live_quote(bars, quote) == []


def test_cross_check_live_quote_skips_when_no_previous_close():
    bars = [make_bar(dt.date(2024, 1, 2), 100)]
    quote = Quote(symbol="TEST", price=100.4, previous_close=None, as_of=dt.datetime.now(dt.timezone.utc))

    assert cross_check_live_quote(bars, quote) == []


def test_check_ohlc_relationships_passes_valid_bars():
    days = business_days(dt.date(2024, 1, 2), 5)
    bars = [make_bar(d, 100 + i) for i, d in enumerate(days)]

    assert check_ohlc_relationships(bars) == []


def test_check_ohlc_relationships_flags_open_outside_range():
    d = dt.date(2024, 1, 2)
    bad = OHLCVBar(date=d, open=110, high=105, low=95, close=100, volume=1_000_000)

    issues = check_ohlc_relationships([bad])

    assert len(issues) == 1
    assert issues[0].type == DataQualityIssueType.INVALID_OHLC_RELATIONSHIP
    assert issues[0].date == d


def test_check_ohlc_relationships_flags_close_outside_range():
    d = dt.date(2024, 1, 2)
    bad = OHLCVBar(date=d, open=100, high=105, low=95, close=120, volume=1_000_000)

    issues = check_ohlc_relationships([bad])

    assert len(issues) == 1
    assert issues[0].type == DataQualityIssueType.INVALID_OHLC_RELATIONSHIP


def test_check_ohlc_relationships_flags_low_greater_than_high():
    d = dt.date(2024, 1, 2)
    bad = OHLCVBar(date=d, open=100, high=95, low=105, close=100, volume=1_000_000)

    issues = check_ohlc_relationships([bad])

    assert len(issues) == 1
    assert issues[0].type == DataQualityIssueType.INVALID_OHLC_RELATIONSHIP


def test_check_history_depth_flags_thin_history():
    days = business_days(dt.date(2024, 1, 2), 30)
    bars = [make_bar(d, 100) for d in days]

    issues = check_history_depth(bars, min_years=5)

    assert len(issues) == 1
    assert issues[0].type == DataQualityIssueType.THIN_HISTORY


def test_check_history_depth_no_flag_when_deep_enough():
    days = business_days(dt.date(2019, 1, 2), 1300)
    bars = [make_bar(d, 100) for d in days]

    assert check_history_depth(bars, min_years=3) == []


def test_validate_price_history_end_to_end_clean_data_passes():
    days = business_days(dt.date(2019, 1, 2), 1300)
    bars = [make_bar(d, 100 + (i % 2) * 0.05) for i, d in enumerate(days)]
    quote = Quote(
        symbol="test",
        price=bars[-1].close,
        previous_close=bars[-1].close,
        as_of=dt.datetime.now(dt.timezone.utc),
    )

    cleaned, report = validate_price_history("test", bars, live_quote=quote)

    assert report.symbol == "TEST"
    assert report.passed is True
    assert report.bar_count == len(bars)
    assert cleaned[0].date == days[0]
    assert cleaned[-1].date == days[-1]


def test_validate_price_history_fails_on_blocking_issues_but_thin_history_alone_does_not():
    days = business_days(dt.date(2024, 1, 2), 10)
    bars = [make_bar(d, 100) for d in days]

    _, report = validate_price_history("test", bars)

    # Only a THIN_HISTORY issue is present (short span) - non-blocking.
    assert all(i.type == DataQualityIssueType.THIN_HISTORY for i in report.issues)
    assert report.passed is True


def test_validate_price_history_dedupes_and_sorts_unordered_input():
    d1, d2 = dt.date(2024, 1, 2), dt.date(2024, 1, 3)
    bars = [make_bar(d2, 101), make_bar(d1, 100), make_bar(d1, 999)]

    cleaned, report = validate_price_history("test", bars)

    assert [b.date for b in cleaned] == [d1, d2]
    assert cleaned[0].close == 100
    assert report.passed is False  # out-of-order + duplicate are blocking
    issue_types = {i.type for i in report.issues}
    assert DataQualityIssueType.OUT_OF_ORDER in issue_types
    assert DataQualityIssueType.DUPLICATE_ROW in issue_types
