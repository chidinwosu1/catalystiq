"""Transaction-cost model shared by every label and every model family.

A "net" return in this system is always *after costs*: spread, slippage,
fees and estimated market impact. Estimating these consistently is what keeps
a weak strategy from looking profitable once you actually trade it, so the
same :class:`CostModel` is applied when generating labels (offline) and when
reasoning about a live setup (online).

The defaults are deliberately conservative round-trip estimates in basis
points, and the model is versioned - changing a coefficient changes
``version``, so a dataset built under one cost regime is never conflated with
another. Market impact scales with participation (trade size relative to
average daily dollar volume), following a standard square-root impact law.
"""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class TradeCosts:
    """Round-trip cost breakdown, expressed as a positive fraction of notional
    (e.g. 0.0012 == 12 bps) plus the component parts for provenance."""

    spread_cost: float
    slippage_cost: float
    fee_cost: float
    impact_cost: float

    @property
    def total(self) -> float:
        return self.spread_cost + self.slippage_cost + self.fee_cost + self.impact_cost


@dataclass(frozen=True)
class CostModel:
    """Configurable, versioned cost estimator. All rates are per side unless
    noted; ``estimate`` returns the round-trip (entry + exit) total."""

    version: str = "1.0.0"
    # Half-spread paid on each side, as a fraction (fallback when a
    # per-symbol estimated spread isn't supplied).
    default_half_spread: float = 0.0005  # 5 bps
    # Slippage beyond the quoted spread, per side.
    slippage_per_side: float = 0.0003  # 3 bps
    # Commission / regulatory fees per side (many brokers are ~0, but SEC/TAF
    # and borrow for shorts are not).
    fee_per_side: float = 0.0001  # 1 bp
    # Extra per-side fee applied to short trades (locate/borrow proxy).
    short_borrow_per_side: float = 0.0002  # 2 bps
    # Square-root market-impact coefficient: impact = coef * sqrt(participation).
    impact_coefficient: float = 0.1
    # Cap on participation used in the impact term (guards against absurd
    # sizes producing runaway estimates).
    max_participation: float = 0.25

    def estimate(
        self,
        *,
        estimated_spread_fraction: float | None = None,
        trade_notional: float = 0.0,
        avg_daily_dollar_volume: float | None = None,
        is_short: bool = False,
    ) -> TradeCosts:
        """Estimate round-trip costs for a trade.

        Parameters
        ----------
        estimated_spread_fraction:
            Symbol-specific full spread as a fraction of price; half is paid
            per side. Falls back to ``2 * default_half_spread`` when absent.
        trade_notional:
            Dollar size of the position (for impact).
        avg_daily_dollar_volume:
            ADV in dollars; participation = notional / ADV.
        is_short:
            Adds the borrow proxy per side.
        """
        full_spread = (
            estimated_spread_fraction
            if estimated_spread_fraction is not None and estimated_spread_fraction >= 0
            else 2 * self.default_half_spread
        )
        # Round trip: half-spread each side => one full spread total.
        spread_cost = full_spread
        slippage_cost = 2 * self.slippage_per_side
        fee_cost = 2 * self.fee_per_side + (2 * self.short_borrow_per_side if is_short else 0.0)

        impact_cost = 0.0
        if trade_notional > 0 and avg_daily_dollar_volume and avg_daily_dollar_volume > 0:
            participation = min(trade_notional / avg_daily_dollar_volume, self.max_participation)
            # Applied on entry and exit.
            impact_cost = 2 * self.impact_coefficient * math.sqrt(max(participation, 0.0))

        return TradeCosts(
            spread_cost=spread_cost,
            slippage_cost=slippage_cost,
            fee_cost=fee_cost,
            impact_cost=impact_cost,
        )


DEFAULT_COST_MODEL = CostModel()
