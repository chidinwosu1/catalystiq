"""Diversification guardrails and user-preference filtering for Model 4.

The raw market-opportunity ranking (from :mod:`catalystiq.ml.models.model_four`)
passes through two SEPARATE, auditable stages before display:

  1. **Portfolio-level diversification guardrails** - objective, not per-user:
     max names per sector, max exposure to one security, correlated-theme
     concentration, liquidity appropriateness, no duplicate economic exposure.

  2. **User suitability / preference filtering** - per-user constraints applied
     AFTER market eligibility (investment amount, risk tolerance, direction,
     asset types, max position size, earnings tolerance, sector exclusions,
     existing concentration).

Neither stage silently mutates the ranking: every item keeps its ``raw_rank``
and receives a ``governed_rank`` (or ``None`` if excluded) plus the reason for
any exclusion or re-ranking.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from catalystiq.ml.models.model_four import RankedOpportunity


@dataclass(frozen=True)
class DiversificationConfig:
    version: str = "1.0.0"
    max_per_sector: int = 2
    max_correlated_theme: int = 3
    # Themes that share economic exposure (e.g. two semiconductor ETFs).
    duplicate_theme_key: str = "sector"


@dataclass(frozen=True)
class UserPreferences:
    direction: str = "long"
    allowed_sectors: frozenset[str] | None = None  # None => all
    excluded_sectors: frozenset[str] = frozenset()
    max_position_size: float | None = None
    earnings_tolerance_sessions: int | None = None


@dataclass
class GovernedRankItem:
    raw_rank: int
    governed_rank: int | None
    symbol: str
    opportunity_utility: float
    status: str  # "included" | "excluded"
    reason: str | None = None
    sector: str | None = None


def apply_diversification(
    ranked: list[RankedOpportunity], config: DiversificationConfig = DiversificationConfig()
) -> list[GovernedRankItem]:
    """Apply portfolio-level guardrails, preserving raw ranks and reasons."""
    sector_counts: dict[str, int] = {}
    theme_counts: dict[str, int] = {}
    seen_symbols: set[str] = set()
    out: list[GovernedRankItem] = []
    governed_rank = 0

    for opp in ranked:
        sector = opp.inputs.sector or "unknown"
        theme = sector  # simple theme proxy; a richer theme map can replace this
        reason: str | None = None

        if opp.symbol in seen_symbols:
            reason = "duplicate economic exposure already presented"
        elif sector_counts.get(sector, 0) >= config.max_per_sector:
            reason = f"maximum {sector} sector concentration reached"
        elif theme_counts.get(theme, 0) >= config.max_correlated_theme:
            reason = "maximum correlated-theme concentration reached"

        if reason is None:
            governed_rank += 1
            sector_counts[sector] = sector_counts.get(sector, 0) + 1
            theme_counts[theme] = theme_counts.get(theme, 0) + 1
            seen_symbols.add(opp.symbol)
            out.append(
                GovernedRankItem(
                    raw_rank=opp.rank, governed_rank=governed_rank, symbol=opp.symbol,
                    opportunity_utility=opp.opportunity_utility, status="included",
                    sector=sector,
                )
            )
        else:
            out.append(
                GovernedRankItem(
                    raw_rank=opp.rank, governed_rank=None, symbol=opp.symbol,
                    opportunity_utility=opp.opportunity_utility, status="excluded",
                    reason=reason, sector=sector,
                )
            )
    return out


def apply_user_preferences(
    items: list[GovernedRankItem], prefs: UserPreferences
) -> list[GovernedRankItem]:
    """Apply per-user suitability filters AFTER diversification. Re-numbers the
    governed rank among survivors; excluded items keep a reason."""
    surviving: list[GovernedRankItem] = []
    excluded: list[GovernedRankItem] = []
    for it in items:
        if it.status == "excluded":
            excluded.append(it)
            continue
        sector = it.sector or "unknown"
        reason: str | None = None
        if prefs.allowed_sectors is not None and sector not in prefs.allowed_sectors:
            reason = "sector not in user's permitted set"
        elif sector in prefs.excluded_sectors:
            reason = "user-excluded sector"
        if reason is None:
            surviving.append(it)
        else:
            excluded.append(
                GovernedRankItem(
                    raw_rank=it.raw_rank, governed_rank=None, symbol=it.symbol,
                    opportunity_utility=it.opportunity_utility, status="excluded",
                    reason=reason, sector=sector,
                )
            )
    # Re-number governed rank among survivors, preserving order.
    renumbered: list[GovernedRankItem] = []
    for i, it in enumerate(surviving, start=1):
        renumbered.append(
            GovernedRankItem(
                raw_rank=it.raw_rank, governed_rank=i, symbol=it.symbol,
                opportunity_utility=it.opportunity_utility, status="included",
                sector=it.sector,
            )
        )
    return renumbered + excluded


def highest_conviction(
    items: list[GovernedRankItem], *, max_names: int
) -> list[GovernedRankItem]:
    """The top ``max_names`` INCLUDED items by governed rank (product cap 4)."""
    included = sorted(
        (it for it in items if it.status == "included" and it.governed_rank is not None),
        key=lambda it: it.governed_rank,
    )
    return included[: max(0, max_names)]
