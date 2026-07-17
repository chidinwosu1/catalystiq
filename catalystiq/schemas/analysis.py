"""Shapes for the real, deterministic technical indicator engine.

Deliberately narrow: these cover only signals computable directly from
price/volume history with a documented formula (§7 "every feature shall
have a mathematical definition"). Nothing here is a probability, a
confidence score, or a rating - those require a calibrated model this
build doesn't have (see catalystiq/analysis/indicators.py's module
docstring). When there isn't enough history for a given indicator, the
reading's status is "insufficient_data" and its value is None - never a
best-guess number.
"""
from __future__ import annotations

import datetime as dt
from typing import Literal

from pydantic import BaseModel

IndicatorStatus = Literal["computed", "insufficient_data"]


class IndicatorReading(BaseModel):
    name: str
    status: IndicatorStatus
    value: float | None = None
    description: str
    params: dict[str, int] = {}
    min_bars_required: int
    percentile_5y: float | None = None
    zscore_5y: float | None = None


class TechnicalSnapshot(BaseModel):
    symbol: str
    as_of: dt.datetime
    bars_used: int
    history_days_available: int
    indicators: list[IndicatorReading]
    warnings: list[str] = []
