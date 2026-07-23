"""Versioned contract for the deterministic Rule-Based Opportunity Score.

This is a TRANSPARENT technical setup-strength score, NOT a probability of
profit, AI confidence, or ML prediction. The `ml` block is always present and
explicitly `not_available` in this release so the future ML products
(net-profit probability, target-before-stop, return range, path/tail risk,
reliability) can be added ALONGSIDE this rule-based score without changing or
replacing it. See catalystiq/analysis/opportunity_score.py.
"""
from __future__ import annotations

import datetime as dt

from pydantic import BaseModel

from catalystiq.schemas.entry_quality import EntryQualityScore


class FactorScore(BaseModel):
    name: str
    score: int | None  # None when the factor is insufficient_data
    max_score: int
    status: str  # "available" | "insufficient_data"
    inputs: dict  # raw inputs used (for transparency)
    explanation: str
    formula_version: str


class UnavailableFactor(BaseModel):
    name: str
    reason: str


class MlStatus(BaseModel):
    status: str  # always "not_available" in this release
    reason: str


class OpportunityScore(BaseModel):
    symbol: str
    status: str  # "available" | "insufficient_data"
    score_type: str  # always "rule_based"
    score: int | None  # total 0..100, or None when insufficient_data
    max_score: int  # 100
    label: str | None  # descriptive band, or None when insufficient_data
    formula_version: str
    calculated_at: dt.datetime
    data_as_of: dt.datetime | None
    freshness: str  # "current" | "stale" | "unknown"
    factor_coverage: str  # e.g. "5/5"
    factors: list[FactorScore]
    unavailable_factors: list[UnavailableFactor]
    warnings: list[str]
    ml: MlStatus
    reason: str | None = None  # populated when status == "insufficient_data"
    # The real-time, intraday Entry Quality Score - INDEPENDENT of this daily
    # Setup Strength. Answers "is this a high-quality MOMENT to enter?" vs Setup
    # Strength's "is this a high-quality STOCK to trade?". None when not computed
    # (e.g. no intraday feed); insufficient_data when intraday inputs are missing.
    entry_quality: EntryQualityScore | None = None


class OpportunityScan(BaseModel):
    """Ranked rule-based candidates from a universe scan. Only symbols with an
    available (fully-eligible) score are candidates; nothing is mock-filled."""

    as_of: dt.datetime
    formula_version: str
    universe_size: int
    eligible_count: int
    top: int
    candidates: list[OpportunityScore]  # ranked by score desc, len <= top
    ml: MlStatus
    note: str | None = None
