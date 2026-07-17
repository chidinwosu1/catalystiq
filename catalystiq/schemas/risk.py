"""Shapes for the Volatility & Risk data product (§7). Measures observable
risk conditions - realized volatility, drawdown, VaR/CVaR, beta/correlation,
risk-adjusted-return ratios, and rule-based risk flags - as distinct from
predicting whether a specific user will make or lose money. See
catalystiq/analysis/risk.py's module docstring for exact formulas and
thresholds.
"""
from __future__ import annotations

import datetime as dt
from typing import Literal

from pydantic import BaseModel

from catalystiq.schemas.analysis import FeatureReading


class RiskFlag(BaseModel):
    flag: str
    severity: Literal["low", "moderate", "high"]
    triggering_value: float | None
    threshold: float | None
    explanation: str
    source_timestamp: dt.datetime


class RiskSnapshot(BaseModel):
    symbol: str
    benchmark_symbol: str | None
    as_of: dt.datetime
    bars_used: int
    history_days_available: int
    metrics: list[FeatureReading]
    flags: list[RiskFlag]
    warnings: list[str] = []
