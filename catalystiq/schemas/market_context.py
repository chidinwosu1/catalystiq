"""Shapes for the Market & Sector Context data product (§14.1 - the
benchmark-relative part only; §14.2 market breadth needs a constituent
universe this build doesn't have, see catalystiq/analysis/market_context.py's
module docstring).
"""
from __future__ import annotations

import datetime as dt

from pydantic import BaseModel

from catalystiq.schemas.analysis import FeatureReading


class MarketContextSnapshot(BaseModel):
    symbol: str
    market_symbol: str | None
    sector_symbol: str | None
    as_of: dt.datetime
    bars_used: int
    history_days_available: int
    metrics: list[FeatureReading]
    warnings: list[str] = []
