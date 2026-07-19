"""Normalized market-calendar shapes (§10).

Provider-agnostic: nothing here leaks the backing exchange-calendar
library's field names, so the operational source can change without changing
callers. A `MarketSession` is one exchange trading session with its regular
open/close, early-close flag, and time zone preserved.
"""
from __future__ import annotations

import datetime as dt

from pydantic import BaseModel


class MarketSession(BaseModel):
    exchange: str
    session_date: dt.date
    # Regular-hours open/close, timezone-aware (in the exchange's own tz).
    open_at: dt.datetime | None = None
    close_at: dt.datetime | None = None
    early_close: bool = False
    holiday_name: str | None = None
    timezone: str
    source: str
    calendar_version: str
    retrieved_at: dt.datetime


class MarketSessionRecord(BaseModel):
    """A normalized Silver market session as returned by the calendar API -
    open/close are UTC instants; `timezone` is the exchange's local zone."""

    exchange: str
    session_date: dt.date
    open_at: dt.datetime | None = None
    close_at: dt.datetime | None = None
    early_close: bool
    holiday_name: str | None = None
    timezone: str
    calendar_version: str
    validation_status: str
    data_quality_warnings: list[dict] | None = None
