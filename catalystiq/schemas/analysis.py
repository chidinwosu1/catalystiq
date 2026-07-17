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
    lineage: Lineage | None = None


# --- Shared contract for every analytical data product added after the
# technical indicator engine above (market structure, risk, volume/
# liquidity, market context, and beyond). Generalizes IndicatorReading's
# status/value/insufficient-data pattern with the full status vocabulary
# from the quantitative-scoring spec's §15.3 feature-status requirement.
# IndicatorReading/TechnicalSnapshot are left untouched (already shipped,
# consumed by the frontend) rather than migrated onto this - new products
# use FeatureReading directly.

FeatureStatus = Literal[
    "available",
    "insufficient_data",
    "not_supported",
    "stale",
    "invalid",
    "provider_unavailable",
    "not_applicable",
]


class FeatureReading(BaseModel):
    """A single computed value or rule-based classification. `value` is a
    plain scalar (int/float/str/bool) for simple metrics; structured,
    multi-field outputs (e.g. a support/resistance level, a risk flag) get
    their own dedicated schema instead of being forced through this shape -
    see each product's schemas module. `int` is listed before `float` in
    the union so whole-number counts (e.g. consecutive-swing counts) stay
    integers instead of being coerced to N.0."""

    name: str
    status: FeatureStatus
    value: int | float | str | bool | None = None
    description: str
    params: dict[str, int | float | str] = {}
    calculation_version: str = "1.0.0"
    percentile_5y: float | None = None
    zscore_5y: float | None = None


class Lineage(BaseModel):
    """Traces a Gold record back through Silver to its Bronze ingestion run,
    per the medallion-architecture requirement: every Gold record must be
    traceable to a calculation version, the Silver data it was built from,
    the Bronze ingestion run behind that, the source provider, and when it
    was calculated. Attached to every Gold snapshot response
    (TechnicalSnapshot, MarketStructureSnapshot, RiskSnapshot,
    VolumeLiquiditySnapshot, MarketContextSnapshot) - see
    catalystiq/pipelines/market_price_pipeline.py's build_gold()."""

    calculation_version: str
    silver_record_count: int
    silver_date_range_start: dt.date | None = None
    silver_date_range_end: dt.date | None = None
    bronze_ingestion_run_id: int | None = None
    source_provider: str
    calculated_at: dt.datetime
