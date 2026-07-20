"""Historical coverage audit + fail-closed on incomplete history."""
import datetime as dt

from catalystiq.ml.history_audit import (
    audit_symbol_coverage,
    feature_coverage_by_period,
    trading_sessions,
)

START = dt.date(2020, 1, 1)
END = dt.date(2020, 6, 30)


def _sessions():
    return trading_sessions(START, END)


def test_trading_sessions_are_weekdays_and_ordered():
    s = _sessions()
    assert s and all(d.weekday() < 5 for d in s)
    assert s == sorted(s)


def test_full_coverage_is_complete():
    cov = audit_symbol_coverage(_sessions(), symbol="AAA", start=START, end=END)
    assert cov.complete is True
    assert cov.missing_sessions == 0
    assert cov.gaps == []
    assert cov.expected_sessions == cov.present_sessions
    assert cov.earliest_bar == _sessions()[0].isoformat()


def test_history_not_reaching_start_fails_closed():
    # Drop the first ~30 sessions -> history starts late.
    sessions = _sessions()
    cov = audit_symbol_coverage(sessions[30:], symbol="AAA", start=START, end=END)
    assert cov.complete is False
    assert any("does not reach the requested start" in r for r in cov.reasons)


def test_large_gap_fails_closed():
    sessions = _sessions()
    # Remove a 20-session block in the middle -> a single large gap.
    with_gap = sessions[:40] + sessions[60:]
    cov = audit_symbol_coverage(with_gap, symbol="AAA", start=START, end=END, max_gap_sessions=5)
    assert cov.complete is False
    assert cov.largest_gap_sessions >= 20
    assert cov.gaps and cov.gaps[0].missing_sessions >= 20
    assert any("gap" in r for r in cov.reasons)


def test_missing_ratio_threshold_fails_closed():
    sessions = _sessions()
    # Drop every 10th session -> ~10% missing, small gaps (<=1) but ratio high.
    sparse = [d for i, d in enumerate(sessions) if i % 10 != 0]
    cov = audit_symbol_coverage(sparse, symbol="AAA", start=START, end=END,
                                max_missing_ratio=0.02, max_gap_sessions=5)
    assert cov.complete is False
    assert any("missing-session ratio" in r for r in cov.reasons)


def test_no_bars_reports_incomplete():
    cov = audit_symbol_coverage([], symbol="ZZZ", start=START, end=END)
    assert cov.complete is False
    assert cov.raw_bar_count == 0
    assert cov.earliest_bar is None


def test_feature_coverage_by_period_buckets_by_year():
    dated = [
        (dt.datetime(2019, 3, 1), {"feature_completeness": 0.8, "adj_close__is_missing": 0}),
        (dt.datetime(2019, 6, 1), {"feature_completeness": 0.6, "adj_close__is_missing": 0}),
        (dt.datetime(2020, 1, 1), {"feature_completeness": 0.9, "adj_close__is_missing": 1}),
    ]
    out = feature_coverage_by_period(dated)
    assert set(out) == {"2019", "2020"}
    assert out["2019"]["examples"] == 2
    assert abs(out["2019"]["mean_completeness"] - 0.7) < 1e-9
    assert out["2019"]["price_present_rate"] == 1.0
    assert out["2020"]["price_present_rate"] == 0.0
