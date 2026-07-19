"""Calibration + evaluation metrics."""
import numpy as np

from catalystiq.ml.calibration import (
    ProbabilityCalibrator,
    calibration_status,
    expected_calibration_error,
    reliability_bins,
)
from catalystiq.ml.evaluation.classification import classification_metrics, roc_auc
from catalystiq.ml.evaluation.quantile import (
    pinball_loss,
    quantile_crossing_detected,
    quantile_metrics,
)
from catalystiq.ml.evaluation.ranking import RankedItem, ranking_metrics


def test_ece_zero_for_perfectly_calibrated():
    # 50/50 outcomes at p=0.5 -> perfectly calibrated single bin.
    probs = [0.5] * 1000
    labels = [1 if i % 2 == 0 else 0 for i in range(1000)]
    assert expected_calibration_error(probs, labels) < 0.02
    assert calibration_status(expected_calibration_error(probs, labels)) == "acceptable"


def test_calibration_status_thresholds():
    assert calibration_status(0.03) == "acceptable"
    assert calibration_status(0.08) == "needs_review"
    assert calibration_status(0.2) == "poor"


def test_isotonic_calibration_improves_ece():
    rng = np.random.default_rng(0)
    s = rng.uniform(0, 1, 4000)
    y = (rng.uniform(0, 1, 4000) < s**2).astype(int)  # miscalibrated raw scores
    before = expected_calibration_error(s, y)
    cal = ProbabilityCalibrator("isotonic").fit(s, y)
    after = expected_calibration_error(cal.transform(s), y)
    assert after < before


def test_reliability_bins_sum_counts():
    probs = [0.1, 0.2, 0.85, 0.9, 0.5]
    labels = [0, 0, 1, 1, 1]
    bins = reliability_bins(probs, labels, n_bins=10)
    assert sum(b.count for b in bins) == len(probs)


def test_roc_auc_perfect_separator():
    y = [0, 0, 1, 1]
    p = [0.1, 0.2, 0.8, 0.9]
    assert roc_auc(y, p) == 1.0


def test_classification_metrics_bundle_keys():
    rng = np.random.default_rng(1)
    p = rng.uniform(0, 1, 500)
    y = (rng.uniform(0, 1, 500) < p).astype(int)
    m = classification_metrics(y, p)
    for k in ["roc_auc", "pr_auc", "precision", "recall", "f1", "brier_score",
              "log_loss", "expected_calibration_error"]:
        assert k in m


def test_pinball_loss_positive():
    assert pinball_loss([0.0, 0.1, -0.1], [0.0, 0.0, 0.0], 0.5) >= 0


def test_quantile_crossing_detection():
    assert quantile_crossing_detected({"q10": -0.04, "q50": 0.01, "q90": 0.05}) is False
    assert quantile_crossing_detected({"q10": 0.05, "q50": 0.01, "q90": -0.02}) is True


def test_quantile_metrics_coverage_monotone_ish():
    rng = np.random.default_rng(2)
    y = rng.normal(0, 1, 2000)
    preds = {
        "q10": np.full(2000, np.quantile(y, 0.10)),
        "q50": np.full(2000, np.quantile(y, 0.50)),
        "q90": np.full(2000, np.quantile(y, 0.90)),
    }
    m = quantile_metrics(y, preds)
    assert m["coverage"]["q10"] < m["coverage"]["q50"] < m["coverage"]["q90"]


def test_ranking_metrics_precision_and_ndcg():
    items = [
        RankedItem("A", 0.9, 0.03, True, "Tech"),
        RankedItem("B", 0.8, -0.01, False, "Tech"),
        RankedItem("C", 0.5, 0.02, True, "Fin"),
        RankedItem("D", 0.2, -0.03, False, "Fin"),
    ]
    m = ranking_metrics(items)
    assert m["precision_at_1"] == 1.0
    assert 0 <= m["ndcg_at_4"] <= 1
    assert m["spearman"] > 0
