"""Normalized BEA shapes (§9). BEA data doesn't fit the macro-observation
model cleanly (it's table/line-oriented), so it has its own shape. Nominal,
real, annualized, and seasonally-adjusted values are never merged without an
explicit classification - the dataset/table/line/frequency and unit fully
qualify each value.
"""
from __future__ import annotations

import datetime as dt

from pydantic import BaseModel


class BeaValue(BaseModel):
    dataset: str
    table_name: str
    line_number: str | None = None
    line_description: str | None = None
    series_code: str | None = None
    time_period: str  # e.g. "2024Q3", "2024"
    frequency: str | None = None  # Q | A | M
    value: float | None = None
    unit: str | None = None  # CL_UNIT
    scale: str | None = None  # UNIT_MULT (power of ten)
    note_ref: str | None = None
    source: str
    retrieved_at: dt.datetime
