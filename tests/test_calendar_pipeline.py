"""NYSE calendar vertical: adapter → Bronze raw doc → Silver market sessions,
plus the /market-calendar/sessions endpoint. No live calls - the adapter is
backed by the offline pandas_market_calendars package."""
import datetime as dt

import pytest

from catalystiq.db import models
from catalystiq.pipelines import calendar_pipeline as cal
from catalystiq.providers.calendars import MarketCalendarProvider, NyseCalendarProvider
from catalystiq.schemas.calendar import MarketSession


class FakeCalendarProvider(MarketCalendarProvider):
    PROVIDER_NAME = "nyse"
    ADAPTER_VERSION = "test"
    EXCHANGE = "NYSE"

    def __init__(self, sessions):
        self._sessions = sessions

    def get_sessions(self, start, end):
        return [s for s in self._sessions if start <= s.session_date <= end]


def _session(date, *, early=False, open_h=13, close_h=20):
    # open/close as tz-aware UTC (13:30 UTC open, 20:00/21:00 UTC close).
    return MarketSession(
        exchange="NYSE",
        session_date=date,
        open_at=dt.datetime(date.year, date.month, date.day, open_h, 30, tzinfo=dt.timezone.utc),
        close_at=dt.datetime(date.year, date.month, date.day, close_h, 0, tzinfo=dt.timezone.utc),
        early_close=early,
        timezone="America/New_York",
        source="nyse",
        calendar_version="4.0.0",
        retrieved_at=dt.datetime(2026, 7, 18, tzinfo=dt.timezone.utc),
    )


def test_real_nyse_adapter_offline():
    # The real adapter must work with no network (pandas_market_calendars is
    # a local package). July 3 2026 is an early close; July 4 falls on a
    # Saturday in 2026 so the holiday is observed Friday July 3.
    provider = NyseCalendarProvider()
    sessions = provider.get_sessions(dt.date(2026, 7, 1), dt.date(2026, 7, 10))
    assert sessions, "expected some July 2026 sessions"
    by_date = {s.session_date: s for s in sessions}
    # No weekend sessions.
    assert all(s.session_date.weekday() < 5 for s in sessions)
    # July 4 2026 is a Saturday -> no session that day.
    assert dt.date(2026, 7, 4) not in by_date
    assert all(s.timezone == "America/New_York" for s in sessions)


def test_ingest_bronze_then_build_silver_idempotent(test_db_session):
    db = test_db_session
    sessions = [_session(dt.date(2026, 7, 6)), _session(dt.date(2026, 7, 7), early=True, close_h=17)]
    provider = FakeCalendarProvider(sessions)

    run = cal.ingest_bronze_sessions(provider, db, dt.date(2026, 7, 6), dt.date(2026, 7, 7))
    assert run.status == "succeeded"
    assert run.record_count == 2
    # Raw document persisted.
    doc = db.query(models.BronzeRawDocument).one()
    assert doc.document_type == "schedule"
    assert len(doc.payload["sessions"]) == 2

    n = cal.build_silver_sessions(db, exchange="NYSE")
    assert n == 2
    rows = cal.get_silver_sessions(db, "NYSE")
    assert [r.session_date for r in rows] == [dt.date(2026, 7, 6), dt.date(2026, 7, 7)]
    assert rows[1].early_close is True
    assert rows[0].stable_identifier == "NYSE:2026-07-06"
    assert rows[0].bronze_ingestion_run_id == run.id

    # Rebuilding from the same doc must not duplicate.
    n2 = cal.build_silver_sessions(db, exchange="NYSE")
    assert n2 == 2
    assert db.query(models.SilverMarketSession).count() == 2


def test_validation_flags_weekend_and_bad_bounds(test_db_session):
    db = test_db_session
    saturday = dt.date(2026, 7, 11)  # a Saturday
    bad = MarketSession(
        exchange="NYSE",
        session_date=saturday,
        open_at=dt.datetime(2026, 7, 11, 20, 0, tzinfo=dt.timezone.utc),
        close_at=dt.datetime(2026, 7, 11, 13, 0, tzinfo=dt.timezone.utc),  # close before open
        timezone="America/New_York",
        source="nyse",
        calendar_version="4.0.0",
        retrieved_at=dt.datetime(2026, 7, 18, tzinfo=dt.timezone.utc),
    )
    provider = FakeCalendarProvider([bad])
    cal.ingest_bronze_sessions(provider, db, saturday, saturday)
    cal.build_silver_sessions(db, exchange="NYSE")

    row = db.query(models.SilverMarketSession).one()
    assert row.validation_status == "clean_with_warnings"
    warning_types = {w["type"] for w in row.data_quality_warnings}
    assert "weekend_session" in warning_types
    assert "invalid_session_bounds" in warning_types


def test_sessions_endpoint(client):
    # Hits the real NYSE adapter (offline) through the router.
    resp = client.get("/market-calendar/sessions?exchange=NYSE&start=2026-07-06&end=2026-07-10")
    assert resp.status_code == 200
    body = resp.json()
    assert body, "expected sessions"
    dates = {row["session_date"] for row in body}
    assert "2026-07-06" in dates  # a Monday
    for row in body:
        assert row["timezone"] == "America/New_York"
        assert row["validation_status"] in ("clean", "clean_with_warnings")


def test_sessions_endpoint_rejects_reversed_range(client):
    resp = client.get("/market-calendar/sessions?start=2026-07-10&end=2026-07-01")
    assert resp.status_code == 422
