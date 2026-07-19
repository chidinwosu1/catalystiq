"""Model 1 - Net-outcome & target-before-stop classifier.

Two SEPARATELY calibrated classifier heads, because "will this trade be net
profitable?" and "will the target be reached before the stop?" are different
outcomes:

  * Head A (net_profit): P(direction-adjusted terminal return, after costs, > 0)
  * Head B (target_before_stop): P(target reached before stop)

Each head trains a logistic baseline and a GBDT candidate, calibrates on the
calibration fold, and approves the candidate only if it materially outperforms
the baseline on the untouched holdout while staying well calibrated. The
predictor NEVER emits a probability from an uncalibrated score.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from catalystiq.ml.dataset.builder import TrainingDataset, TrainingExample
from catalystiq.ml.models.base import to_matrix
from catalystiq.ml.models.heads import BinaryHead, BinaryHeadResult, train_binary_head
from catalystiq.ml.models.training import (
    ChronoSplit,
    SplitReport,
    chronological_split,
    matrices_for,
    stable_feature_names,
)


@dataclass
class Model1Prediction:
    net_profit_probability: float
    target_before_stop_probability: float
    calibration_status: str
    comparable_sample_count: int


@dataclass
class Model1Artifact:
    feature_names: list[str]
    net_profit_head: BinaryHead
    target_before_stop_head: BinaryHead
    calibration_status: str
    comparable_sample_count: int

    def predict(self, features: dict) -> Model1Prediction:
        X = to_matrix([features], self.feature_names)
        return Model1Prediction(
            net_profit_probability=float(self.net_profit_head.predict_proba(X)[0]),
            target_before_stop_probability=float(self.target_before_stop_head.predict_proba(X)[0]),
            calibration_status=self.calibration_status,
            comparable_sample_count=self.comparable_sample_count,
        )


@dataclass
class Model1TrainingReport:
    horizon_days: int
    direction: str
    split: SplitReport
    net_profit: dict
    target_before_stop: dict
    artifact: Model1Artifact | None = None
    warnings: list[str] = field(default_factory=list)


def _net_profit_label(ex: TrainingExample):
    return ex.labels.net_profit_label


def _tbs_label(ex: TrainingExample):
    return ex.labels.target_before_stop_label


def train_model_one(
    dataset: TrainingDataset,
    *,
    horizon_days: int,
    direction: str = "long",
    min_comparable: int = 200,
) -> Model1TrainingReport:
    """Train both heads on ``dataset`` (already filtered to one horizon/direction)."""
    feature_names = stable_feature_names(dataset)
    split = chronological_split(dataset)
    report = Model1TrainingReport(
        horizon_days=horizon_days,
        direction=direction,
        split=SplitReport.from_split(dataset, split),
        net_profit={},
        target_before_stop={},
    )

    if not split.train_idx or not split.calib_idx or not split.holdout_idx:
        report.warnings.append("insufficient chronological data for a train/calib/holdout split")
        return report

    heads: dict[str, BinaryHeadResult] = {}
    for name, getter in (("net_profit", _net_profit_label), ("target_before_stop", _tbs_label)):
        X_tr, y_tr, _ = matrices_for(dataset, feature_names, split.train_idx, getter)
        X_cal, y_cal, _ = matrices_for(dataset, feature_names, split.calib_idx, getter)
        X_ho, y_ho, _ = matrices_for(dataset, feature_names, split.holdout_idx, getter)
        if len(np.unique(y_tr)) < 2 or len(np.unique(y_cal)) < 2 or len(y_ho) == 0:
            report.warnings.append(f"{name}: not enough class variety for a calibrated head")
            continue
        result = train_binary_head(
            X_train=X_tr, y_train=y_tr,
            X_calib=X_cal, y_calib=y_cal,
            X_eval=X_ho, y_eval=y_ho,
            feature_names=feature_names,
        )
        heads[name] = result

    report.net_profit = _head_report(heads.get("net_profit"))
    report.target_before_stop = _head_report(heads.get("target_before_stop"))

    if "net_profit" in heads and "target_before_stop" in heads:
        # Overall calibration status is the worse of the two heads.
        statuses = [heads["net_profit"].calibration_status, heads["target_before_stop"].calibration_status]
        overall = "poor" if "poor" in statuses else ("needs_review" if "needs_review" in statuses else "acceptable")
        comparable = min(report.split.n_holdout, report.split.n_train)
        report.artifact = Model1Artifact(
            feature_names=feature_names,
            net_profit_head=heads["net_profit"].head,
            target_before_stop_head=heads["target_before_stop"].head,
            calibration_status=overall,
            comparable_sample_count=comparable,
        )
        if comparable < min_comparable:
            report.warnings.append(
                f"comparable sample count {comparable} below minimum {min_comparable} "
                "- artifact must not be approved for user-facing use"
            )
    return report


def _head_report(result: BinaryHeadResult | None) -> dict:
    if result is None:
        return {"status": "not_trained"}
    return {
        "chosen": result.chosen,
        "candidate_approved": result.candidate_approved,
        "calibration_status": result.calibration_status,
        "baseline_metrics": result.baseline_metrics,
        "candidate_metrics": result.candidate_metrics,
        "notes": result.approval_notes,
    }
