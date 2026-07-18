"""Market-calendar endpoints (§18). Read-only Gold-style reads over the
market-session Silver product: bring Silver up to date on demand (ingest the
schedule only when the requested range isn't already covered), then serve
normalized sessions.
"""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from catalystiq.auth import verify_action_key
from catalystiq.db.base import get_db
from catalystiq.pipelines.calendar_pipeline import (
    ensure_sessions,
    get_silver_sessions,
)
from catalystiq.providers.calendars import (
    CalendarError,
    MarketCalendarProvider,
    get_calendar_provider,
)
from catalystiq.schemas.calendar import MarketSessionRecord

router = APIRouter(
    prefix="/market-calendar",
    tags=["market-calendar"],
    dependencies=[Depends(verify_action_key)],
)


def _record(row) -> MarketSessionRecord:
    def as_utc(value: dt.datetime | None) -> dt.datetime | None:
        return value.replace(tzinfo=dt.timezone.utc) if value is not None else None

    return MarketSessionRecord(
        exchange=row.exchange,
        session_date=row.session_date,
        open_at=as_utc(row.open_at),
        close_at=as_utc(row.close_at),
        early_close=row.early_close,
        holiday_name=row.holiday_name,
        timezone=row.timezone,
        calendar_version=row.calendar_version,
        validation_status=row.validation_status,
        data_quality_warnings=row.data_quality_warnings,
    )


@router.get("/sessions", response_model=list[MarketSessionRecord])
def get_sessions(
    exchange: str = Query(default="NYSE"),
    start: dt.date | None = Query(default=None),
    end: dt.date | None = Query(default=None),
    provider: MarketCalendarProvider = Depends(get_calendar_provider),
    db: Session = Depends(get_db),
):
    """Normalized exchange sessions in [start, end] (defaults: last 5 days
    through 30 days ahead). Ingests the schedule only when the range isn't
    already covered in Silver, so repeated calls don't re-ingest."""
    today = dt.date.today()
    start = start or today - dt.timedelta(days=5)
    end = end or today + dt.timedelta(days=30)
    if start > end:
        raise HTTPException(status_code=422, detail="start must be on or before end")

    existing = get_silver_sessions(db, exchange, start=start, end=end)
    covered = {row.session_date for row in existing}
    # Re-ingest only if we have no coverage at all for the window; the
    # schedule is deterministic, so partial coverage of trading days is
    # expected (weekends/holidays have no session).
    if not covered:
        try:
            ensure_sessions(provider, db, start, end, exchange=exchange)
        except CalendarError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        existing = get_silver_sessions(db, exchange, start=start, end=end)

    return [_record(row) for row in existing]
