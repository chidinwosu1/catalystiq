"""Shapes for the Market Structure data product (§6): swing points, trend
structure, support/resistance, breakout/breakdown state, gaps, and a
rule-based market regime classification. Every classification here is a
deterministic rule over real OHLCV data - see
catalystiq/analysis/market_structure.py's module docstring for the exact
rules and thresholds. Nothing here is a probability or a prediction.
"""
from __future__ import annotations

import datetime as dt
from typing import Literal

from pydantic import BaseModel

from catalystiq.schemas.analysis import FeatureReading


class SwingPoint(BaseModel):
    kind: Literal["high", "low"]
    date: dt.date
    price: float
    pivot_strength: int
    confirmed: bool
    bars_since: int


class SupportResistanceLevel(BaseModel):
    price: float
    type: Literal["support", "resistance"]
    method: str
    touch_count: int
    first_observed_at: dt.date
    last_tested_at: dt.date
    distance_from_price_pct: float
    status: Literal["active", "broken"]
    strength_score: int


class MarketStructureSnapshot(BaseModel):
    symbol: str
    as_of: dt.datetime
    bars_used: int
    history_days_available: int
    swing_highs: list[SwingPoint]
    swing_lows: list[SwingPoint]
    trend_structure: FeatureReading
    consecutive_higher_highs: FeatureReading
    consecutive_higher_lows: FeatureReading
    consecutive_lower_highs: FeatureReading
    consecutive_lower_lows: FeatureReading
    bars_since_structural_change: FeatureReading
    support_resistance_levels: list[SupportResistanceLevel]
    breakout_state: FeatureReading
    gap_readings: list[FeatureReading]
    regime: FeatureReading
    warnings: list[str] = []
