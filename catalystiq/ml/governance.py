"""Cross-model consistency governance.

After the model families return their outputs, this layer checks them against
each other for material conflicts and derives a GOVERNED decision status. A
material conflict must prevent a high-conviction result. The governed status
is an evidence label - ``enter_candidate`` is NOT authorization to trade; the
existing Review -> Confirm order controls remain required regardless.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class GovernedStatus(str, Enum):
    ENTER_CANDIDATE = "enter_candidate"
    WATCH = "watch"
    WAIT = "wait"
    AVOID = "avoid"
    ABSTAIN = "abstain"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


@dataclass
class CrossModelInputs:
    net_profit_probability: float | None = None
    target_before_stop_probability: float | None = None
    median_net_return: float | None = None
    severe_downside: float | None = None            # e.g. -0.05
    stop_breach_probability: float | None = None
    gap_beyond_stop_probability: float | None = None
    realized_volatility_percentile: float | None = None  # 0..100
    predicted_risk_low: bool | None = None
    reliability_score: int | None = None
    comparable_sample_count: int | None = None
    quantile_valid: bool = True


@dataclass
class GovernanceResult:
    status: GovernedStatus
    conflicts: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    high_conviction_blocked: bool = False


# Thresholds for conflict detection (documented, tunable).
HIGH_PROFIT_PROB = 0.6
HIGH_TBS_PROB = 0.6
HIGH_STOP_BREACH = 0.4
SEVERE_DOWNSIDE = -0.06
HIGH_VOL_PCTILE = 85.0
LOW_RELIABILITY = 40
MIN_COMPARABLES = 100


def detect_conflicts(inp: CrossModelInputs) -> list[str]:
    """Enumerate material cross-model conflicts."""
    conflicts: list[str] = []

    if (
        inp.net_profit_probability is not None
        and inp.median_net_return is not None
        and inp.net_profit_probability >= HIGH_PROFIT_PROB
        and inp.median_net_return < 0
    ):
        conflicts.append("High profit probability with a negative median return")

    if (
        inp.median_net_return is not None
        and inp.severe_downside is not None
        and inp.median_net_return > 0
        and inp.severe_downside <= SEVERE_DOWNSIDE
    ):
        conflicts.append("Positive median return with disproportionate severe downside")

    if (
        inp.target_before_stop_probability is not None
        and inp.stop_breach_probability is not None
        and inp.target_before_stop_probability >= HIGH_TBS_PROB
        and inp.stop_breach_probability >= HIGH_STOP_BREACH
    ):
        conflicts.append("High target-before-stop probability with high stop-breach probability")

    if (
        inp.predicted_risk_low
        and inp.realized_volatility_percentile is not None
        and inp.realized_volatility_percentile >= HIGH_VOL_PCTILE
    ):
        conflicts.append("Low predicted risk during extreme realized volatility")

    if (
        inp.reliability_score is not None
        and inp.comparable_sample_count is not None
        and inp.reliability_score >= 70
        and inp.comparable_sample_count < MIN_COMPARABLES
    ):
        conflicts.append("High reliability with few comparable historical observations")

    return conflicts


def govern(inp: CrossModelInputs, *, abstain_status: str | None = None) -> GovernanceResult:
    """Derive the governed status.

    ``abstain_status`` (from the reliability layer) short-circuits to
    ``abstain`` / ``insufficient_evidence`` when set. Otherwise conflicts and
    the outputs drive watch/wait/avoid/enter_candidate.
    """
    if abstain_status == "insufficient_evidence":
        return GovernanceResult(GovernedStatus.INSUFFICIENT_EVIDENCE,
                                reasons=["Reliability layer returned insufficient_evidence"],
                                high_conviction_blocked=True)
    if abstain_status == "abstain":
        return GovernanceResult(GovernedStatus.ABSTAIN,
                                reasons=["Reliability layer returned abstain"],
                                high_conviction_blocked=True)

    if not inp.quantile_valid:
        return GovernanceResult(GovernedStatus.ABSTAIN,
                                reasons=["Quantile ordering validation failed"],
                                high_conviction_blocked=True)

    conflicts = detect_conflicts(inp)
    high_conviction_blocked = bool(conflicts)
    reasons: list[str] = list(conflicts)

    # Base decision from the evidence (only reached when no abstain condition).
    npp = inp.net_profit_probability
    med = inp.median_net_return
    severe = inp.severe_downside

    if conflicts:
        # A material conflict prevents high conviction; fall back to watch/avoid.
        if severe is not None and severe <= SEVERE_DOWNSIDE:
            status = GovernedStatus.AVOID
        else:
            status = GovernedStatus.WATCH
    elif npp is None or med is None:
        status = GovernedStatus.WAIT
        reasons.append("Incomplete model outputs")
    elif npp >= HIGH_PROFIT_PROB and med > 0 and (severe is None or severe > SEVERE_DOWNSIDE):
        status = GovernedStatus.ENTER_CANDIDATE
        reasons.append("Favorable, self-consistent model evidence (not trade authorization)")
    elif npp >= 0.5 and med > 0:
        status = GovernedStatus.WATCH
    elif med is not None and med <= 0:
        status = GovernedStatus.AVOID
    else:
        status = GovernedStatus.WAIT

    return GovernanceResult(
        status=status,
        conflicts=conflicts,
        reasons=reasons,
        high_conviction_blocked=high_conviction_blocked,
    )
