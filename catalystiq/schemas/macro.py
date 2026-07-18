"""Normalized macroeconomic shapes (§7, §9). Provider-agnostic: FRED and (in
Phase 3) BLS/BEA observations normalize into the same MacroObservation, so
downstream code never depends on a specific agency's field names.

Point-in-time is first-class: a MacroObservation carries the vintage window
(realtime_start/realtime_end) it was known within, kept distinct from the
observation date it describes and from when we retrieved it.
"""
from __future__ import annotations

import datetime as dt

from pydantic import BaseModel


class MacroSeries(BaseModel):
    series_id: str
    title: str | None = None
    frequency: str | None = None
    units: str | None = None
    seasonal_adjustment: str | None = None
    observation_start: dt.date | None = None
    observation_end: dt.date | None = None
    last_updated: dt.datetime | None = None
    notes: str | None = None
    source: str
    retrieved_at: dt.datetime


class MacroObservation(BaseModel):
    series_id: str
    observation_date: dt.date
    value: float | None = None  # FRED uses "." for missing -> None, never fabricated
    # ALFRED vintage window this value was the known value within.
    realtime_start: dt.date | None = None
    realtime_end: dt.date | None = None
    units: str | None = None
    frequency: str | None = None
    seasonal_adjustment: str | None = None
    # Provider-specific source fields preserved verbatim (e.g. BLS footnotes,
    # period code, preliminary/revised flag) so normalizing into the shared
    # shape never loses a source's own metadata (§8).
    source_fields: dict | None = None
    source: str
    retrieved_at: dt.datetime


class EconomicRelease(BaseModel):
    release_id: str
    name: str | None = None
    # Scheduled release date vs. actual publication timestamp are distinct
    # concepts (§7) and never conflated.
    scheduled_date: dt.date | None = None
    actual_published_at: dt.datetime | None = None
    press_release: bool | None = None
    link: str | None = None
    source: str
    retrieved_at: dt.datetime
