"""Versioned contract for the Dynamic Entry Quality Score.

This is a TRANSPARENT, real-time intraday score answering *"is this a
high-quality MOMENT to enter?"* - deliberately independent of the daily
Rule-Based Opportunity Score (Setup Strength), which answers *"is this a
high-quality STOCK to trade?"*. A stock can hold a strong Setup Strength while
having a poor Entry Quality when it is extended after a large morning rally.

Like the Setup Strength contract it is NOT a probability of profit, AI
confidence, or ML prediction, and never a buy/sell instruction. A missing,
stale, or insufficient intraday input NEVER counts as a bearish zero: the
owning component is marked ``insufficient_data`` and (v1) the whole score is
returned as ``insufficient_data`` rather than fabricating or renormalizing.

See catalystiq/analysis/entry_quality.py.
"""
from __future__ import annotations

import datetime as dt

from pydantic import BaseModel


class EntryQualityComponent(BaseModel):
    name: str
    score: int | None  # None when the component is insufficient_data
    max_score: int
    status: str  # "available" | "insufficient_data"
    inputs: dict  # raw intraday inputs used (for transparency)
    explanation: str
    formula_version: str


class EntryQualityScore(BaseModel):
    """A real-time 0..100 read of how attractive the *current moment* is as an
    entry, computed from intraday bars only. Independent of Setup Strength."""

    symbol: str
    status: str  # "available" | "insufficient_data"
    score_type: str  # always "entry_quality"
    score: int | None  # total 0..100, or None when insufficient_data
    max_score: int  # 100
    rating: str | None  # "Excellent Entry" .. "Poor Entry", or None
    formula_version: str
    calculated_at: dt.datetime
    data_as_of: dt.datetime | None  # timestamp of the last intraday bar used
    interval: str | None  # intraday bar interval, e.g. "5m" / "15m"
    component_coverage: str  # e.g. "7/7"
    components: list[EntryQualityComponent]
    warnings: list[str]
    reason: str | None = None  # populated when status == "insufficient_data"
