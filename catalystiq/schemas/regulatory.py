"""Normalized regulatory shapes (§11, §12). Short interest and daily
short-sale volume are DISTINCT datasets and never conflated (§11).
"""
from __future__ import annotations

import datetime as dt

from pydantic import BaseModel


class ShortSaleVolume(BaseModel):
    """One symbol's daily short-sale volume from a FINRA reg-SHO file."""

    symbol: str
    trade_date: dt.date
    short_volume: int | None = None
    short_exempt_volume: int | None = None
    total_volume: int | None = None
    reporting_facility: str | None = None
    file_version: str = "original"
    source: str
    retrieved_at: dt.datetime


class ShortInterest(BaseModel):
    """One symbol's semi-monthly short interest position."""

    symbol: str
    settlement_date: dt.date
    publication_date: dt.date | None = None
    short_interest_quantity: int | None = None
    previous_short_interest_quantity: int | None = None
    average_daily_volume: float | None = None
    days_to_cover: float | None = None
    file_version: str = "original"
    source: str
    retrieved_at: dt.datetime


class SecurityMasterEntry(BaseModel):
    """A symbol-directory listing (§12). `internal_security_id` is a stable
    internal key - ticker alone is not permanent (can change / be reused)."""

    internal_security_id: str
    symbol: str
    name: str | None = None
    exchange: str | None = None
    listing_market: str | None = None
    etf: bool | None = None
    test_issue: bool | None = None
    is_active: bool = True
    source: str
    retrieved_at: dt.datetime
