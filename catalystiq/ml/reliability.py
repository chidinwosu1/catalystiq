"""Reliability assessment and abstention.

Reliability is DELIBERATELY SEPARATE from the probability of profit. A trade
can have a high modelled profit probability while the *prediction itself* is
unreliable (thin comparable history, stale data, out-of-distribution setup,
poor calibration, model disagreement). This module scores that reliability and
decides when to abstain.

``reliability_score`` is a 0-100 index built from weighted components; it is
NOT a probability and must never be presented as one. ``should_abstain``
returns an ``abstain`` / ``insufficient_evidence`` decision when any hard
condition is met.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class ReliabilityLabel(str, Enum):
    HIGH = "high"
    MODERATE = "moderate"
    LOW = "low"
    INSUFFICIENT = "insufficient"


@dataclass
class ReliabilityInputs:
    feature_completeness: float | None = None       # 0..1
    data_freshness_ok: bool | None = None           # within tolerance?
    comparable_sample_count: int | None = None
    out_of_distribution: bool | None = None
    calibration_ok: bool | None = None
    recent_oos_performance: float | None = None     # 0..1 (e.g. recent hit rate)
    regime_represented: bool | None = None
    prediction_range_width: float | None = None     # smaller = tighter = better
    model_agreement: float | None = None            # 0..1 agreement across families
    min_comparable: int = 200


@dataclass
class ReliabilityResult:
    score: int
    label: ReliabilityLabel
    reasons: list[str] = field(default_factory=list)


# Component weights (sum ~1.0). Reliability is an index, not a probability.
_WEIGHTS = {
    "feature_completeness": 0.18,
    "data_freshness": 0.12,
    "comparable_sample": 0.18,
    "in_distribution": 0.12,
    "calibration": 0.14,
    "recent_oos": 0.10,
    "regime": 0.06,
    "range_tightness": 0.05,
    "agreement": 0.05,
}


def assess_reliability(inp: ReliabilityInputs) -> ReliabilityResult:
    reasons: list[str] = []
    score = 0.0
    weight_used = 0.0

    def add(key: str, value: float, good_msg: str, bad_msg: str, bad_below: float = 0.5):
        nonlocal score, weight_used
        w = _WEIGHTS[key]
        weight_used += w
        score += w * max(0.0, min(1.0, value))
        if value >= bad_below:
            reasons.append(good_msg)
        else:
            reasons.append(bad_msg)

    if inp.feature_completeness is not None:
        add("feature_completeness", inp.feature_completeness,
            "High feature completeness", "Limited feature completeness")
    if inp.data_freshness_ok is not None:
        add("data_freshness", 1.0 if inp.data_freshness_ok else 0.0,
            "Data is fresh", "Data is stale")
    if inp.comparable_sample_count is not None:
        ratio = min(1.0, inp.comparable_sample_count / max(1, inp.min_comparable))
        add("comparable_sample", ratio,
            "Adequate comparable historical sample",
            "Limited comparable historical examples")
    if inp.out_of_distribution is not None:
        add("in_distribution", 0.0 if inp.out_of_distribution else 1.0,
            "Setup is within the training distribution",
            "Setup is materially out of distribution")
    if inp.calibration_ok is not None:
        add("calibration", 1.0 if inp.calibration_ok else 0.0,
            "Acceptable classifier calibration", "Classifier calibration is weak")
    if inp.recent_oos_performance is not None:
        add("recent_oos", inp.recent_oos_performance,
            "Recent out-of-sample performance is solid",
            "Recent out-of-sample performance is weak")
    if inp.regime_represented is not None:
        add("regime", 1.0 if inp.regime_represented else 0.0,
            "Current market regime is represented",
            "Current market regime is under-represented")
    if inp.prediction_range_width is not None:
        # Map width to tightness in [0,1]; wide ranges reduce reliability.
        tightness = max(0.0, min(1.0, 1.0 - inp.prediction_range_width))
        add("range_tightness", tightness,
            "Prediction range is reasonably tight", "Prediction range is wide")
    if inp.model_agreement is not None:
        add("agreement", inp.model_agreement,
            "The model families agree", "The model families disagree")

    final = int(round(100 * (score / weight_used))) if weight_used > 0 else 0
    label = _label(final, inp)
    return ReliabilityResult(score=final, label=label, reasons=reasons)


def _label(score: int, inp: ReliabilityInputs) -> ReliabilityLabel:
    if (
        inp.comparable_sample_count is not None
        and inp.comparable_sample_count < inp.min_comparable // 2
    ):
        return ReliabilityLabel.INSUFFICIENT
    if score >= 75:
        return ReliabilityLabel.HIGH
    if score >= 50:
        return ReliabilityLabel.MODERATE
    if score >= 25:
        return ReliabilityLabel.LOW
    return ReliabilityLabel.INSUFFICIENT


@dataclass
class AbstentionDecision:
    abstain: bool
    status: str  # "ok" | "insufficient_evidence" | "abstain"
    reasons: list[str] = field(default_factory=list)


def should_abstain(
    inp: ReliabilityInputs,
    *,
    required_artifact_missing: bool = False,
    quantile_validation_failed: bool = False,
    outputs_conflict: bool = False,
) -> AbstentionDecision:
    """Hard abstention gate. Any triggered condition forces abstention.

    Returns ``insufficient_evidence`` for evidence-quality failures and
    ``abstain`` for conflicts / calibration failures.
    """
    reasons: list[str] = []
    insufficient = False
    abstain = False

    if required_artifact_missing:
        reasons.append("A required model artifact is missing")
        insufficient = True
    if inp.feature_completeness is not None and inp.feature_completeness < 0.5:
        reasons.append("Critical features are missing")
        insufficient = True
    if inp.data_freshness_ok is False:
        reasons.append("Critical features are stale")
        insufficient = True
    if inp.out_of_distribution:
        reasons.append("The current setup is materially outside the training distribution")
        abstain = True
    if inp.comparable_sample_count is not None and inp.comparable_sample_count < inp.min_comparable // 2:
        reasons.append("Comparable sample support is inadequate")
        insufficient = True
    if inp.calibration_ok is False:
        reasons.append("Probability calibration failed")
        abstain = True
    if quantile_validation_failed:
        reasons.append("Quantile ordering validation failed")
        abstain = True
    if inp.regime_represented is False:
        reasons.append("The current market regime was not sufficiently represented")
        abstain = True
    if outputs_conflict:
        reasons.append("Model outputs materially conflict")
        abstain = True

    if abstain:
        return AbstentionDecision(True, "abstain", reasons)
    if insufficient:
        return AbstentionDecision(True, "insufficient_evidence", reasons)
    return AbstentionDecision(False, "ok", reasons)
