"""Point-in-time eligible-stock universe construction (Model 4 & ranking).

Before any cross-sectional ranking happens, the candidate universe is
constructed *as it existed at the ranking timestamp*. Eligibility is decided
by objective, configurable market criteria; every exclusion records a reason
so the decision is auditable and never silent.

Critically for training, the universe must be point-in-time: a historical
ranking date must use the securities that were listed and tradable THEN,
including names later delisted - never today's surviving universe (that is
survivorship bias, and it inflates backtests). This module operates purely on
a caller-supplied snapshot of candidate metadata; it does not query providers.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from enum import Enum


class AssetType(str, Enum):
    COMMON_STOCK = "common_stock"
    ETF = "etf"
    LEVERAGED_INVERSE_ETF = "leveraged_inverse_etf"
    OTC = "otc"
    OTHER = "other"


@dataclass(frozen=True)
class UniverseConfig:
    """Objective eligibility thresholds. Configurable and versioned."""

    version: str = "1.0.0"
    min_price: float = 5.0
    min_avg_daily_dollar_volume: float = 5_000_000.0
    max_estimated_spread_bps: float = 50.0
    min_history_bars: int = 400  # ~ enough for 200-SMA + validation
    allow_etfs: bool = True
    allow_leveraged_inverse_etfs: bool = False
    max_feature_staleness_days: int = 5


@dataclass(frozen=True)
class CandidateSnapshot:
    """Point-in-time metadata about one candidate at the ranking timestamp.

    The caller (a later, approved data-wiring phase) is responsible for
    populating this from point-in-time sources. It is provider-neutral."""

    symbol: str
    asset_type: AssetType
    price: float | None
    avg_daily_dollar_volume: float | None
    estimated_spread_bps: float | None
    history_bars: int
    is_tradable: bool  # not suspended/halted/delisted at this timestamp
    listed: bool       # point-in-time listed (False for pre-IPO/post-delist)
    sector: str | None
    feature_staleness_days: float | None
    next_earnings_in_sessions: int | None = None
    symbol_mapping_conflict: bool = False


@dataclass(frozen=True)
class EligibilityDecision:
    symbol: str
    eligible: bool
    reasons: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class UniverseMember:
    symbol: str
    sector: str | None
    snapshot: CandidateSnapshot


def evaluate_candidate(
    snap: CandidateSnapshot,
    config: UniverseConfig,
    *,
    ranking_timestamp: dt.datetime | None = None,
    earnings_tolerance_sessions: int | None = None,
) -> EligibilityDecision:
    """Apply objective market eligibility (NOT user preferences) to one
    candidate. User-preference filtering is a separate, later stage.

    ``earnings_tolerance_sessions`` is included here only when the user's
    event-risk preference has been resolved upstream; passing ``None`` means
    "do not exclude on earnings proximity at the market-eligibility stage".
    """
    reasons: list[str] = []

    if not snap.listed:
        reasons.append("not point-in-time listed")
    if not snap.is_tradable:
        reasons.append("suspended/halted/delisted or not tradable")
    if snap.symbol_mapping_conflict:
        reasons.append("stale/conflicting symbol mapping")

    if snap.asset_type is AssetType.OTC:
        reasons.append("OTC securities excluded")
    if snap.asset_type is AssetType.OTHER:
        reasons.append("asset type not permitted")
    if snap.asset_type is AssetType.ETF and not config.allow_etfs:
        reasons.append("ETFs not permitted by config")
    if snap.asset_type is AssetType.LEVERAGED_INVERSE_ETF and not config.allow_leveraged_inverse_etfs:
        reasons.append("leveraged/inverse ETFs not permitted")

    if snap.price is None or snap.price < config.min_price:
        reasons.append(f"price below minimum ({config.min_price})")
    if (
        snap.avg_daily_dollar_volume is None
        or snap.avg_daily_dollar_volume < config.min_avg_daily_dollar_volume
    ):
        reasons.append("insufficient average daily dollar volume (illiquid)")
    if (
        snap.estimated_spread_bps is None
        or snap.estimated_spread_bps > config.max_estimated_spread_bps
    ):
        reasons.append("estimated spread too wide")
    if snap.history_bars < config.min_history_bars:
        reasons.append("inadequate historical training coverage")

    if (
        snap.feature_staleness_days is None
        or snap.feature_staleness_days > config.max_feature_staleness_days
    ):
        reasons.append("data stale or incomplete")

    if (
        earnings_tolerance_sessions is not None
        and snap.next_earnings_in_sessions is not None
        and 0 <= snap.next_earnings_in_sessions <= earnings_tolerance_sessions
    ):
        reasons.append("upcoming earnings violates event-risk preference")

    return EligibilityDecision(symbol=snap.symbol, eligible=not reasons, reasons=reasons)


def build_eligible_universe(
    candidates: list[CandidateSnapshot],
    config: UniverseConfig,
    *,
    ranking_timestamp: dt.datetime | None = None,
    earnings_tolerance_sessions: int | None = None,
) -> tuple[list[UniverseMember], list[EligibilityDecision]]:
    """Return ``(eligible_members, all_decisions)``.

    ``all_decisions`` preserves the eligible/excluded verdict and reasons for
    every candidate, so the pipeline can report ``universe_size`` and
    ``eligible_count`` and audit exclusions.
    """
    decisions: list[EligibilityDecision] = []
    members: list[UniverseMember] = []
    for snap in candidates:
        decision = evaluate_candidate(
            snap,
            config,
            ranking_timestamp=ranking_timestamp,
            earnings_tolerance_sessions=earnings_tolerance_sessions,
        )
        decisions.append(decision)
        if decision.eligible:
            members.append(UniverseMember(symbol=snap.symbol, sector=snap.sector, snapshot=snap))
    return members, decisions
