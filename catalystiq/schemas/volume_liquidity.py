"""Shapes for the Volume & Liquidity data product (§8)."""
from __future__ import annotations

import datetime as dt
from typing import Literal

from pydantic import BaseModel

from catalystiq.schemas.analysis import FeatureReading, Lineage

LiquidityClass = Literal["high", "moderate", "low", "very_low", "unknown"]


class VolumeLiquiditySnapshot(BaseModel):
    symbol: str
    as_of: dt.datetime
    bars_used: int
    history_days_available: int
    metrics: list[FeatureReading]
    liquidity_classification: FeatureReading
    warnings: list[str] = []
    lineage: Lineage | None = None
