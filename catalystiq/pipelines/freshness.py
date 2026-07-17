"""Calendar-aware freshness policy for the price-bar Silver layer.

Replaces the flat `max_age_hours=24` rule with an actual definition of
"stale": for daily bars, Silver is fresh once it contains the most recent
NYSE session whose regular-hours close has already passed. Weekends and
holidays have no session in the exchange calendar, so they're naturally
skipped - no special-casing needed, and no ingestion fires on them.

Every caller in this codebase requests `interval="1d"` today; `SilverPriceBar`
has no intraday timestamp column at all (just a `date`), so a genuine
intraday freshness check isn't something this schema can honestly answer -
`is_stale()` raises `NotImplementedError` for any other interval rather
than faking a tolerance check against data that was never captured with
intraday granularity.
"""
from __future__ import annotations

import datetime as dt

from catalystiq.analysis.config import DEFAULT_FRESHNESS_CONFIG


class FreshnessPolicy:
    def __init__(self, calendar_name: str | None = None):
        self._calendar_name = calendar_name or DEFAULT_FRESHNESS_CONFIG.daily_session_calendar

    def latest_expected_session(self, as_of: dt.datetime) -> dt.date | None:
        """The most recent session in the exchange calendar whose regular-
        hours close is at or before `as_of`. None if no session has closed
        yet within the lookback window (i.e. nothing to require)."""
        import pandas_market_calendars as mcal

        if as_of.tzinfo is None:
            as_of = as_of.replace(tzinfo=dt.timezone.utc)

        calendar = mcal.get_calendar(self._calendar_name)
        start = (as_of - dt.timedelta(days=14)).date()
        end = as_of.date()
        schedule = calendar.schedule(start_date=start.isoformat(), end_date=end.isoformat())
        if schedule.empty:
            return None

        closed_sessions = schedule[schedule["market_close"] <= as_of]
        if closed_sessions.empty:
            return None
        return closed_sessions.index[-1].date()

    def is_stale(
        self,
        latest_silver_date: dt.date | None,
        as_of: dt.datetime,
        interval: str = "1d",
    ) -> bool:
        """Whether Silver needs a fresh Bronze->Silver build. `interval`
        must be "1d" - see module docstring."""
        if interval != "1d":
            raise NotImplementedError(
                f"FreshnessPolicy only supports interval='1d' - Silver has no "
                f"intraday timestamp column to evaluate {interval!r} freshness "
                "against."
            )

        if latest_silver_date is None:
            return True

        expected = self.latest_expected_session(as_of)
        if expected is None:
            # No session has closed yet in the lookback window - nothing
            # new could possibly be expected, so don't force a re-ingest.
            return False
        return latest_silver_date < expected
