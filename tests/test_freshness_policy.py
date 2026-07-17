import datetime as dt

import pytest

from catalystiq.pipelines.freshness import FreshnessPolicy


def test_weekend_does_not_require_a_newer_session():
    policy = FreshnessPolicy()
    # 2026-07-18 is a Saturday; the prior Friday (2026-07-17) is a real
    # NYSE session that's already closed by noon UTC.
    saturday_noon_utc = dt.datetime(2026, 7, 18, 12, 0, tzinfo=dt.timezone.utc)
    assert policy.latest_expected_session(saturday_noon_utc) == dt.date(2026, 7, 17)
    assert policy.is_stale(dt.date(2026, 7, 17), saturday_noon_utc) is False


def test_weekend_stale_data_still_flagged():
    policy = FreshnessPolicy()
    saturday_noon_utc = dt.datetime(2026, 7, 18, 12, 0, tzinfo=dt.timezone.utc)
    # Missing Friday's session (Thursday is one session behind).
    assert policy.is_stale(dt.date(2026, 7, 16), saturday_noon_utc) is True


def test_holiday_does_not_require_a_newer_session():
    policy = FreshnessPolicy()
    # 2026-01-01 (New Year's Day) is an NYSE holiday. 2025-12-31 was the
    # last session before it.
    holiday_afternoon_utc = dt.datetime(2026, 1, 1, 18, 0, tzinfo=dt.timezone.utc)
    assert policy.latest_expected_session(holiday_afternoon_utc) == dt.date(2025, 12, 31)
    assert policy.is_stale(dt.date(2025, 12, 31), holiday_afternoon_utc) is False


def test_mid_session_before_close_does_not_require_todays_bar():
    policy = FreshnessPolicy()
    # Friday 2026-07-17, 18:00 UTC - before the 20:00 UTC regular close.
    before_close = dt.datetime(2026, 7, 17, 18, 0, tzinfo=dt.timezone.utc)
    assert policy.latest_expected_session(before_close) == dt.date(2026, 7, 16)
    assert policy.is_stale(dt.date(2026, 7, 16), before_close) is False


def test_after_close_requires_todays_bar():
    policy = FreshnessPolicy()
    after_close = dt.datetime(2026, 7, 17, 21, 0, tzinfo=dt.timezone.utc)
    assert policy.latest_expected_session(after_close) == dt.date(2026, 7, 17)
    assert policy.is_stale(dt.date(2026, 7, 16), after_close) is True
    assert policy.is_stale(dt.date(2026, 7, 17), after_close) is False


def test_no_silver_data_is_always_stale():
    policy = FreshnessPolicy()
    now = dt.datetime(2026, 7, 17, 21, 0, tzinfo=dt.timezone.utc)
    assert policy.is_stale(None, now) is True


def test_intraday_interval_raises_not_implemented():
    policy = FreshnessPolicy()
    now = dt.datetime(2026, 7, 17, 21, 0, tzinfo=dt.timezone.utc)
    with pytest.raises(NotImplementedError):
        policy.is_stale(dt.date(2026, 7, 17), now, interval="5m")
