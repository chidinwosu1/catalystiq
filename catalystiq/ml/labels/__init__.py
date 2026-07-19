"""Outcome-label, barrier and transaction-cost generators.

These modules turn raw point-in-time price paths into the supervised targets
the five model families train against, applying the same executable-entry and
cost assumptions everywhere so no two models are trained on inconsistent
outcomes.
"""
from catalystiq.ml.labels.costs import CostModel, DEFAULT_COST_MODEL, TradeCosts
from catalystiq.ml.labels.barriers import (
    BarrierOutcome,
    BothTouchedPolicy,
    compute_barrier_outcome,
)
from catalystiq.ml.labels.outcomes import (
    Direction,
    OutcomeLabels,
    TARGET_DEFINITIONS,
    generate_outcome_labels,
)

__all__ = [
    "CostModel",
    "DEFAULT_COST_MODEL",
    "TradeCosts",
    "BarrierOutcome",
    "BothTouchedPolicy",
    "compute_barrier_outcome",
    "Direction",
    "OutcomeLabels",
    "TARGET_DEFINITIONS",
    "generate_outcome_labels",
]
