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

from pydantic import BaseModel, field_validator

from catalystiq.schemas.entry_quality import EntryQualityScore

# Canonical trading styles / holding periods, risk tolerances, and directions.
# Mirror the frontend Define-Preferences page (see frontend/src/lib/preferences.tsx).
_STYLES = ("intraday", "day", "swing", "long")
_RISKS = ("conservative", "moderate", "aggressive")
_DIRECTIONS = ("long", "both")

# Free-text asset-class labels (from the UI or an API caller) normalized to the
# governed classes used by catalystiq.analysis.sectors.asset_class(). Only
# "stock" and "etf" are actually scannable today; option/future are accepted so
# an explicit selection filters correctly (to an honest empty set) rather than
# being silently ignored.
_ASSET_ALIAS = {
    "stock": "stock", "stocks": "stock", "equity": "stock", "equities": "stock",
    "etf": "etf", "etfs": "etf",
    "option": "option", "options": "option",
    "future": "future", "futures": "future",
}


def normalize_asset_class(label: str) -> str | None:
    """Map a raw asset-class label to a governed class, or None if unrecognized."""
    return _ASSET_ALIAS.get(str(label).strip().lower())


class ScanPreferences(BaseModel):
    """User investing preferences that personalize the opportunity scan.

    These are the exact values collected on the Define-Preferences page. Unlike
    the previous behavior (where preferences never left the browser), the scan
    now receives them and applies each one to eligibility and/or ranking. Every
    field has a safe default so a caller may send a partial set.
    """

    style: str = "swing"          # holding period: intraday|day|swing|long
    risk: str = "moderate"        # conservative|moderate|aggressive
    amount: float = 10_000.0      # investable capital, USD
    max_loss_pct: float = 5.0     # max acceptable loss per position, %
    direction: str = "long"       # long|both
    assets: tuple[str, ...] = ("stock",)  # normalized governed classes
    fractional_shares: bool = True        # broker supports fractional shares
    constraints: str = ""                 # free-text; no automated effect yet

    @field_validator("style")
    @classmethod
    def _v_style(cls, v: str) -> str:
        v = str(v).strip().lower()
        return v if v in _STYLES else "swing"

    @field_validator("risk")
    @classmethod
    def _v_risk(cls, v: str) -> str:
        v = str(v).strip().lower()
        return v if v in _RISKS else "moderate"

    @field_validator("direction")
    @classmethod
    def _v_direction(cls, v: str) -> str:
        v = str(v).strip().lower()
        return v if v in _DIRECTIONS else "long"

    @field_validator("amount")
    @classmethod
    def _v_amount(cls, v: float) -> float:
        return max(0.0, float(v))

    @field_validator("max_loss_pct")
    @classmethod
    def _v_max_loss(cls, v: float) -> float:
        # Clamp to a sane (0, 100] band; 0 or absurd values would make every
        # setup ineligible / eligible for the wrong reason.
        return min(100.0, max(0.01, float(v)))

    @field_validator("assets", mode="before")
    @classmethod
    def _v_assets(cls, v) -> tuple[str, ...]:
        if v is None:
            return ("stock",)
        if isinstance(v, str):
            raw = [p for p in v.split(",")]
        else:
            raw = list(v)
        out: list[str] = []
        for item in raw:
            norm = normalize_asset_class(item)
            if norm and norm not in out:
                out.append(norm)
        # An empty/all-unrecognized selection means "no asset class chosen" -
        # keep it empty so personalization returns an honest empty set rather
        # than silently defaulting to stocks.
        return tuple(out)


class PersonalizationInfo(BaseModel):
    """Per-candidate explanation of how the active preferences were applied.

    Present only on candidates returned from a personalized scan. It makes the
    effect of each preference auditable end-to-end (tests assert on it, and the
    UI can surface it) rather than the score changing invisibly.
    """

    direction: str                       # "long" | "short" - the setup's bias
    asset_class: str                     # "stock" | "etf"
    reference_price: float | None        # last close used for sizing
    base_score: int                      # the generic rule-based score
    personalized_score: int              # holding-period / direction weighted
    atr_pct: float | None                # volatility used by the risk gate
    stop_distance_pct: float | None      # style-scaled ATR stop distance
    est_shares: float | None             # position size given capital & risk
    est_position_value: float | None
    est_max_loss: float | None           # $ at risk if the stop is hit
    applied: list[str]                   # human-readable notes on what applied


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
    # How the active user preferences were applied to this candidate. None on a
    # generic (non-personalized) scan; populated on a personalized scan.
    personalization: PersonalizationInfo | None = None


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
    # Machine-readable state so the client can act without parsing ``note``:
    #   "ok"          - a real scan (candidates, or a genuine "nothing qualifies")
    #   "warming"     - the first scan is still computing in the background
    #   "unavailable" - the scan ran but market data could not be fetched for the
    #                   universe (e.g. the upstream provider is rate-limiting), so
    #                   0 candidates reflects a data outage, NOT true ineligibility
    # Defaults to "ok" for backward compatibility with existing serialized scans.
    status: str = "ok"
