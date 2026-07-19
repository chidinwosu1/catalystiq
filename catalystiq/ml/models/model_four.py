"""Model 4 - Cross-sectional stock opportunity ranker.

Selects and ranks the best eligible stocks for a given direction and horizon,
answering "which eligible stocks offer the strongest RISK-ADJUSTED opportunity
relative to the others right now?" - not merely "highest probability of
profit".

Two governed stages of modelling:

  * a transparent, versioned BASELINE composite over already-approved Model
    1-3 outputs and the rule-based score (:func:`baseline_opportunity_utility`),
  * an optional learning-to-rank CANDIDATE (pointwise GBDT on realized utility;
    pairwise/listwise are noted extension points).

Two hard training safeguards:

  * Model 1-3 predictions used as ranker features MUST be OUT-OF-FOLD
    historical predictions - never in-sample - or they leak unrealistically
    accurate information. The trainer requires each example to be flagged
    ``oof=True`` and refuses otherwise.
  * The realized-utility TARGET is finalized/versioned here and must not use
    any information unavailable at the ranking timestamp.

Diversification guardrails and user-preference filtering are applied AFTER
ranking, in :mod:`catalystiq.ml.ranking_governance` - this module produces the
raw market-opportunity ranking only.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


TARGET_VERSION = "1.0.0"


@dataclass(frozen=True)
class RankerWeights:
    """Initial governed default weights for the baseline composite. These are
    configurable and versioned; changing them changes ``version``."""

    version: str = "1.0.0"
    net_profit_probability: float = 0.25
    target_before_stop_probability: float = 0.20
    normalized_median_net_return: float = 0.20
    normalized_rule_based_setup_strength: float = 0.15
    model_reliability: float = 0.10
    sector_and_market_alignment: float = 0.10
    severe_downside_penalty: float = 1.0
    stop_breach_penalty: float = 0.30
    gap_risk_penalty: float = 0.50
    liquidity_and_cost_penalty: float = 1.0


DEFAULT_RANKER_WEIGHTS = RankerWeights()


@dataclass(frozen=True)
class OpportunityInputs:
    """Per-candidate inputs to the composite, all from approved model outputs
    and point-in-time context. Normalized fields are expected in [0,1] (or
    small signed returns for median_net_return)."""

    symbol: str
    net_profit_probability: float
    target_before_stop_probability: float
    normalized_median_net_return: float
    normalized_rule_based_setup_strength: float
    model_reliability: float  # 0..1
    sector_and_market_alignment: float  # 0..1
    severe_downside: float  # negative return magnitude, e.g. -0.04
    stop_breach_probability: float
    gap_risk_probability: float
    liquidity_cost_penalty: float  # 0..1, higher = costlier/less liquid
    sector: str | None = None


def baseline_opportunity_utility(
    inp: OpportunityInputs, weights: RankerWeights = DEFAULT_RANKER_WEIGHTS
) -> float:
    """Transparent governed composite (initial ranking before an ML ranker is
    validated). Higher is better."""
    reward = (
        weights.net_profit_probability * inp.net_profit_probability
        + weights.target_before_stop_probability * inp.target_before_stop_probability
        + weights.normalized_median_net_return * inp.normalized_median_net_return
        + weights.normalized_rule_based_setup_strength * inp.normalized_rule_based_setup_strength
        + weights.model_reliability * inp.model_reliability
        + weights.sector_and_market_alignment * inp.sector_and_market_alignment
    )
    penalty = (
        weights.severe_downside_penalty * abs(min(0.0, inp.severe_downside))
        + weights.stop_breach_penalty * inp.stop_breach_probability
        + weights.gap_risk_penalty * inp.gap_risk_probability
        + weights.liquidity_and_cost_penalty * inp.liquidity_cost_penalty
    )
    return float(reward - penalty)


def realized_utility(
    *,
    net_terminal_return: float,
    severe_downside: float,
    stop_breach: int,
    gap_risk: int,
    transaction_cost: float,
    downside_weight: float = 1.0,
    stop_breach_weight: float = 0.02,
    gap_weight: float = 0.02,
) -> float:
    """Versioned training target: risk-adjusted realized utility of one trade.

    realized_utility = net_terminal_return - downside_penalty
                       - stop_breach_penalty - gap_risk_penalty
                       - transaction_cost_penalty

    Uses only realized outcomes computed under the same executable-entry and
    cost conventions as Models 1-3. Never a forward Sharpe of a single trade.
    """
    return float(
        net_terminal_return
        - downside_weight * abs(min(0.0, severe_downside))
        - stop_breach_weight * float(stop_breach)
        - gap_weight * float(gap_risk)
        - transaction_cost
    )


@dataclass
class RankedOpportunity:
    rank: int
    symbol: str
    opportunity_utility: float
    inputs: OpportunityInputs


@dataclass
class Model4Ranker:
    """Ranks candidates. ``mode`` is 'baseline_composite' unless an approved
    learning-to-rank model is supplied."""

    weights: RankerWeights = DEFAULT_RANKER_WEIGHTS
    _model: object | None = None
    _feature_names: list[str] | None = None
    mode: str = "baseline_composite"

    def score(self, inp: OpportunityInputs) -> float:
        if self._model is not None and self._feature_names is not None:
            from catalystiq.ml.models.base import to_matrix

            X = to_matrix([_inputs_to_row(inp)], self._feature_names)
            return float(self._model.predict(X)[0])
        return baseline_opportunity_utility(inp, self.weights)

    def rank(self, candidates: list[OpportunityInputs]) -> list[RankedOpportunity]:
        scored = [(self.score(c), c) for c in candidates]
        scored.sort(key=lambda t: t[0], reverse=True)
        return [
            RankedOpportunity(rank=i + 1, symbol=c.symbol, opportunity_utility=s, inputs=c)
            for i, (s, c) in enumerate(scored)
        ]


def _inputs_to_row(inp: OpportunityInputs) -> dict:
    return {
        "net_profit_probability": inp.net_profit_probability,
        "target_before_stop_probability": inp.target_before_stop_probability,
        "normalized_median_net_return": inp.normalized_median_net_return,
        "normalized_rule_based_setup_strength": inp.normalized_rule_based_setup_strength,
        "model_reliability": inp.model_reliability,
        "sector_and_market_alignment": inp.sector_and_market_alignment,
        "severe_downside": inp.severe_downside,
        "stop_breach_probability": inp.stop_breach_probability,
        "gap_risk_probability": inp.gap_risk_probability,
        "liquidity_cost_penalty": inp.liquidity_cost_penalty,
    }


@dataclass
class Model4TrainingReport:
    chosen: str
    candidate_approved: bool
    n_examples: int
    ranking_metrics: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    ranker: Model4Ranker | None = None


@dataclass(frozen=True)
class RankerExample:
    inputs: OpportunityInputs
    realized_utility: float
    oof: bool  # were the M1-3 features generated out-of-fold?


def train_model_four(
    examples: list[RankerExample], *, weights: RankerWeights = DEFAULT_RANKER_WEIGHTS
) -> Model4TrainingReport:
    """Train a pointwise learning-to-rank candidate on realized utility.

    Refuses to train if any example's Model 1-3 features are NOT out-of-fold,
    because in-sample predictions leak. Approves the candidate over the
    baseline composite only if it improves rank correlation with realized
    utility on a chronological split (caller supplies chronologically ordered
    examples).
    """
    report = Model4TrainingReport(chosen="baseline_composite", candidate_approved=False,
                                  n_examples=len(examples))
    if any(not ex.oof for ex in examples):
        report.warnings.append(
            "refusing to train Model 4: some Model 1-3 features are in-sample, not "
            "out-of-fold (would leak). Regenerate with out-of-fold predictions."
        )
        report.ranker = Model4Ranker(weights=weights)
        return report
    if len(examples) < 100:
        report.warnings.append("too few ranker examples; using baseline composite")
        report.ranker = Model4Ranker(weights=weights)
        return report

    try:
        from sklearn.ensemble import HistGradientBoostingRegressor

        from catalystiq.ml.evaluation.ranking import RankedItem, spearman_rank_correlation
        from catalystiq.ml.models.base import to_matrix

        feature_names = list(_inputs_to_row(examples[0].inputs).keys())
        n = len(examples)
        cut = int(n * 0.75)  # chronological (caller orders by time)
        rows = [_inputs_to_row(ex.inputs) for ex in examples]
        X = to_matrix(rows, feature_names)
        y = np.array([ex.realized_utility for ex in examples], dtype=float)
        model = HistGradientBoostingRegressor(max_depth=3, max_iter=200, learning_rate=0.05)
        model.fit(X[:cut], y[:cut])

        # Evaluate rank correlation on the held-out tail.
        preds = model.predict(X[cut:])
        cand_items = [
            RankedItem(examples[cut + i].inputs.symbol, float(preds[i]), float(y[cut + i]), y[cut + i] > 0)
            for i in range(len(preds))
        ]
        base_items = [
            RankedItem(ex.inputs.symbol, baseline_opportunity_utility(ex.inputs, weights),
                       ex.realized_utility, ex.realized_utility > 0)
            for ex in examples[cut:]
        ]
        cand_sp = spearman_rank_correlation(cand_items)
        base_sp = spearman_rank_correlation(base_items)
        report.ranking_metrics = {"candidate_spearman": cand_sp, "baseline_spearman": base_sp}
        if not np.isnan(cand_sp) and not np.isnan(base_sp) and cand_sp > base_sp + 0.02:
            ranker = Model4Ranker(weights=weights, _model=model, _feature_names=feature_names,
                                  mode="ml_cross_sectional")
            report.chosen = "ml_cross_sectional"
            report.candidate_approved = True
            report.ranker = ranker
        else:
            report.warnings.append("ML ranker did not beat baseline composite; baseline retained")
            report.ranker = Model4Ranker(weights=weights)
    except Exception as exc:
        report.warnings.append(f"ranker candidate training failed, using baseline: {exc}")
        report.ranker = Model4Ranker(weights=weights)
    return report
