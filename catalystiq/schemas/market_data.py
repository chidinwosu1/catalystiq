"""Pydantic shapes returned by MarketDataProvider implementations.

These are provider-agnostic on purpose: nothing here should leak
Yahoo-Finance-specific field names, so swapping in another provider
(§1.1 "abstracted behind a MarketDataProvider ... interface so either
is swappable later") never changes a caller's shape.
"""
from __future__ import annotations

import datetime as dt

from pydantic import BaseModel


class OHLCVBar(BaseModel):
    date: dt.date
    open: float
    high: float
    low: float
    close: float
    volume: int


class Quote(BaseModel):
    symbol: str
    price: float
    previous_close: float | None = None
    as_of: dt.datetime


class QuoteResult(BaseModel):
    """One symbol's entry in a batch quote response. A per-symbol failure is
    reported as status="unavailable" (never fabricated) so one bad ticker
    doesn't fail the whole batch."""

    symbol: str
    status: str  # "ok" | "unavailable"
    price: float | None = None
    previous_close: float | None = None
    change: float | None = None
    change_pct: float | None = None
    as_of: dt.datetime | None = None
    detail: str | None = None


class FundamentalsSnapshot(BaseModel):
    symbol: str
    long_name: str | None = None
    sector: str | None = None
    industry: str | None = None
    market_cap: float | None = None
    trailing_pe: float | None = None
    forward_pe: float | None = None
    peg_ratio: float | None = None
    ev_to_ebitda: float | None = None
    revenue_growth: float | None = None
    earnings_growth: float | None = None
    gross_margins: float | None = None
    operating_margins: float | None = None
    return_on_equity: float | None = None
    free_cashflow: float | None = None
    total_debt: float | None = None
    total_cash: float | None = None
    as_of: dt.datetime


class NewsItem(BaseModel):
    headline: str
    source_url: str
    published_at: dt.datetime
    category: str | None = None
    summary: str | None = None


class SymbolSearchResult(BaseModel):
    symbol: str
    instrument_name: str | None = None
    exchange: str | None = None
    instrument_type: str | None = None
    country: str | None = None
    currency: str | None = None


class ExchangeInfo(BaseModel):
    name: str
    code: str | None = None
    country: str | None = None
    timezone: str | None = None
