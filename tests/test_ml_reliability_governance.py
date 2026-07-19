"""Reliability, abstention and cross-model governance."""
from catalystiq.ml.reliability import (
    ReliabilityInputs,
    ReliabilityLabel,
    assess_reliability,
    should_abstain,
)
from catalystiq.ml.governance import (
    CrossModelInputs,
    GovernedStatus,
    detect_conflicts,
    govern,
)


def test_reliability_high_when_all_good():
    r = assess_reliability(ReliabilityInputs(
        feature_completeness=1.0, data_freshness_ok=True, comparable_sample_count=1000,
        out_of_distribution=False, calibration_ok=True, recent_oos_performance=0.9,
        regime_represented=True, prediction_range_width=0.1, model_agreement=0.9,
    ))
    assert r.label == ReliabilityLabel.HIGH
    assert r.score >= 75


def test_reliability_insufficient_on_thin_sample():
    r = assess_reliability(ReliabilityInputs(comparable_sample_count=10, min_comparable=200))
    assert r.label == ReliabilityLabel.INSUFFICIENT


def test_abstain_on_missing_artifact():
    d = should_abstain(ReliabilityInputs(), required_artifact_missing=True)
    assert d.abstain and d.status == "insufficient_evidence"


def test_abstain_on_calibration_failure():
    d = should_abstain(ReliabilityInputs(calibration_ok=False))
    assert d.abstain and d.status == "abstain"


def test_abstain_on_quantile_failure():
    d = should_abstain(ReliabilityInputs(), quantile_validation_failed=True)
    assert d.abstain and d.status == "abstain"


def test_conflict_high_profit_negative_median():
    conflicts = detect_conflicts(CrossModelInputs(net_profit_probability=0.7, median_net_return=-0.01))
    assert any("negative median" in c for c in conflicts)


def test_conflict_blocks_high_conviction():
    res = govern(CrossModelInputs(
        net_profit_probability=0.7, median_net_return=-0.01, severe_downside=-0.02,
        quantile_valid=True,
    ))
    assert res.high_conviction_blocked
    assert res.status != GovernedStatus.ENTER_CANDIDATE


def test_enter_candidate_when_clean_and_favorable():
    res = govern(CrossModelInputs(
        net_profit_probability=0.66, median_net_return=0.02, severe_downside=-0.02,
        stop_breach_probability=0.2, quantile_valid=True,
    ))
    assert res.status == GovernedStatus.ENTER_CANDIDATE


def test_quantile_invalid_forces_abstain():
    res = govern(CrossModelInputs(quantile_valid=False))
    assert res.status == GovernedStatus.ABSTAIN


def test_abstain_status_short_circuits():
    res = govern(CrossModelInputs(net_profit_probability=0.9, median_net_return=0.1),
                 abstain_status="insufficient_evidence")
    assert res.status == GovernedStatus.INSUFFICIENT_EVIDENCE
