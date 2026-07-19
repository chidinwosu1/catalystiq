"""Versioned outcome-label generation for all five model families.

This is the single place the supervised targets are defined, so every model
trains against consistent, cost-aware, look-ahead-free labels. Each target
has a stable definition string and version; changing a definition bumps the
version and therefore the dataset's ``target_definition_version``.

Targets produced here
----------------------
Model 1  net_profit_label            1 if net terminal return > 0 else 0
         target_before_stop_label    from the barrier computation
Model 2  net_terminal_return         direction-adjusted, after costs
Model 3  max_adverse_excursion       direction-aware (worst intrabar)
         max_favorable_excursion     direction-aware (best intrabar)
         stop_breach_label           stop reached/passed during hold
         gap_beyond_stop_label       next executable price gapped through stop
         (lower_tail_terminal_return is a *cross-example* quantile of
          net_terminal_return, computed by the Model 3 pipeline, not here)

Executable entry
----------------
For end-of-day analysis the prediction is made at the session close, but the
trade is entered at the NEXT session's executable opening price. The caller
supplies ``executable_entry_price`` (that next open); this module never
assumes entry at a price that was already required to compute the prediction.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Sequence

from catalystiq.ml import TARGET_DEFINITION_VERSION
from catalystiq.ml.labels.barriers import (
    Bar,
    BarrierOutcome,
    BothTouchedPolicy,
    compute_barrier_outcome,
)
from catalystiq.ml.labels.costs import CostModel, DEFAULT_COST_MODEL


class Direction(str, Enum):
    LONG = "long"
    SHORT = "short"


TARGET_DEFINITIONS: dict[str, str] = {
    "version": TARGET_DEFINITION_VERSION,
    "net_profit_label": (
        "1 when direction-adjusted terminal return minus spread, slippage, "
        "fees and estimated market impact > 0, else 0"
    ),
    "target_before_stop_label": (
        "1 when the predetermined target is reached before the predetermined "
        "stop, else 0; excluded/stop-first on ambiguous same-bar touches"
    ),
    "net_terminal_return": (
        "direction-adjusted return from executable entry to horizon exit minus "
        "spread, slippage, fees and estimated market impact"
    ),
    "max_adverse_excursion": "direction-aware worst intrabar excursion vs entry",
    "max_favorable_excursion": "direction-aware best intrabar excursion vs entry",
    "stop_breach_label": "1 when the stop is reached or passed during the hold",
    "gap_beyond_stop_label": "1 when the next executable price is worse than the stop",
}


@dataclass(frozen=True)
class OutcomeLabels:
    symbol: str
    direction: Direction
    horizon_days: int
    executable_entry_price: float
    target_price: float
    stop_price: float

    # Model 1
    net_profit_label: int | None
    target_before_stop_label: int | None
    # Model 2
    net_terminal_return: float | None
    # Model 3
    max_adverse_excursion: float | None
    max_favorable_excursion: float | None
    stop_breach_label: int | None
    gap_beyond_stop_label: int | None

    # Provenance / bookkeeping
    gross_terminal_return: float
    round_trip_cost: float
    both_touched: bool
    excluded_reason: str | None
    target_definition_version: str = TARGET_DEFINITION_VERSION

    @property
    def is_complete(self) -> bool:
        """A fully-labeled example (nothing excluded/undecidable)."""
        return (
            self.net_profit_label is not None
            and self.net_terminal_return is not None
            and self.target_before_stop_label is not None
        )


def generate_outcome_labels(
    *,
    symbol: str,
    direction: str,
    horizon_days: int,
    executable_entry_price: float,
    target_price: float,
    stop_price: float,
    path: Sequence[Bar],
    estimated_spread_fraction: float | None = None,
    trade_notional: float = 0.0,
    avg_daily_dollar_volume: float | None = None,
    cost_model: CostModel = DEFAULT_COST_MODEL,
    both_touched_policy: BothTouchedPolicy = BothTouchedPolicy.STOP_FIRST,
) -> OutcomeLabels:
    """Produce the full label set for one historical training example.

    ``path`` is the ordered OHLC sequence over the holding period, beginning
    at the entry session. Costs are subtracted from the gross direction-
    adjusted return to form the net return that drives the profit label.
    """
    d = Direction(direction)
    is_short = d is Direction.SHORT

    barrier: BarrierOutcome = compute_barrier_outcome(
        direction=direction,
        entry_price=executable_entry_price,
        target_price=target_price,
        stop_price=stop_price,
        path=path,
        both_touched_policy=both_touched_policy,
    )

    costs = cost_model.estimate(
        estimated_spread_fraction=estimated_spread_fraction,
        trade_notional=trade_notional,
        avg_daily_dollar_volume=avg_daily_dollar_volume,
        is_short=is_short,
    )

    gross = barrier.terminal_return
    net_terminal_return: float | None
    net_profit_label: int | None
    if not path:
        net_terminal_return = None
        net_profit_label = None
    else:
        net_terminal_return = gross - costs.total
        net_profit_label = 1 if net_terminal_return > 0 else 0

    tbs = barrier.target_before_stop
    target_before_stop_label = None if tbs is None else int(tbs)

    return OutcomeLabels(
        symbol=symbol,
        direction=d,
        horizon_days=horizon_days,
        executable_entry_price=executable_entry_price,
        target_price=target_price,
        stop_price=stop_price,
        net_profit_label=net_profit_label,
        target_before_stop_label=target_before_stop_label,
        net_terminal_return=net_terminal_return,
        max_adverse_excursion=barrier.max_adverse_excursion if path else None,
        max_favorable_excursion=barrier.max_favorable_excursion if path else None,
        stop_breach_label=int(barrier.stop_breach) if path else None,
        gap_beyond_stop_label=int(barrier.gap_beyond_stop) if path else None,
        gross_terminal_return=gross,
        round_trip_cost=costs.total,
        both_touched=barrier.both_touched,
        excluded_reason=barrier.excluded_reason,
    )
