"""Shapes for the Data Validation Layer (§2.9)."""
from __future__ import annotations

import datetime as dt
from enum import Enum

from pydantic import BaseModel


class DataQualityIssueType(str, Enum):
    OUT_OF_ORDER = "out_of_order"
    DUPLICATE_ROW = "duplicate_row"
    MISSING_TRADING_DAY = "missing_trading_day"
    ABNORMAL_GAP = "abnormal_gap"
    LIVE_QUOTE_MISMATCH = "live_quote_mismatch"
    THIN_HISTORY = "thin_history"
    INVALID_OHLC_RELATIONSHIP = "invalid_ohlc_relationship"


class DataQualityIssue(BaseModel):
    type: DataQualityIssueType
    date: dt.date | None = None
    detail: str


class DataQualityReport(BaseModel):
    symbol: str
    passed: bool
    issues: list[DataQualityIssue]
    checked_at: dt.datetime
    bar_count: int
