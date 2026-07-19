"""Model 2 - Distributional net-return model (quantile regression).

Predicts a range of plausible net returns (q10..q90) over the holding period
rather than a single average, because extreme winners can make a weak strategy
look profitable on the mean. Quantile ordering (q10<=q25<=q50<=q75<=q90) is
enforced in the head; a result is never exposed if ordering validation fails.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from catalystiq.ml.dataset.builder import TrainingDataset, TrainingExample
from catalystiq.ml.evaluation.quantile import quantile_crossing_detected
from catalystiq.ml.models.base import to_matrix
from catalystiq.ml.models.heads import QuantileHead, QuantileHeadResult, train_quantile_head
from catalystiq.ml.models.training import (
    SplitReport,
    chronological_split,
    matrices_for,
    stable_feature_names,
)


@dataclass
class Model2Prediction:
    net_return_quantiles: dict[str, float]
    expected_net_return: float
    quantile_crossing_detected: bool

    @property
    def valid(self) -> bool:
        return not self.quantile_crossing_detected


@dataclass
class Model2Artifact:
    feature_names: list[str]
    head: QuantileHead

    def predict(self, features: dict) -> Model2Prediction:
        X = to_matrix([features], self.feature_names)
        preds = self.head.predict(X)
        quantiles = {lvl: float(v[0]) for lvl, v in preds.items()}
        crossing = quantile_crossing_detected(quantiles)
        # Median-anchored expected value proxy; UI emphasises median/downside.
        expected = float(np.mean(list(quantiles.values())))
        return Model2Prediction(
            net_return_quantiles=quantiles,
            expected_net_return=expected,
            quantile_crossing_detected=crossing,
        )


@dataclass
class Model2TrainingReport:
    horizon_days: int
    direction: str
    split: SplitReport
    quantile: dict
    artifact: Model2Artifact | None = None
    warnings: list[str] = field(default_factory=list)


def _net_return(ex: TrainingExample):
    return ex.labels.net_terminal_return


def train_model_two(
    dataset: TrainingDataset, *, horizon_days: int, direction: str = "long"
) -> Model2TrainingReport:
    feature_names = stable_feature_names(dataset)
    split = chronological_split(dataset)
    report = Model2TrainingReport(
        horizon_days=horizon_days, direction=direction,
        split=SplitReport.from_split(dataset, split), quantile={},
    )
    if not split.train_idx or not split.holdout_idx:
        report.warnings.append("insufficient chronological data for a train/holdout split")
        return report

    X_tr, y_tr, _ = matrices_for(dataset, feature_names, split.train_idx, _net_return)
    X_ho, y_ho, _ = matrices_for(dataset, feature_names, split.holdout_idx, _net_return)
    if len(y_tr) < 30 or len(y_ho) == 0:
        report.warnings.append("too few labeled return examples")
        return report

    result: QuantileHeadResult = train_quantile_head(
        X_train=X_tr, y_train=y_tr, X_eval=X_ho, y_eval=y_ho, feature_names=feature_names
    )
    report.quantile = {
        "chosen": result.chosen,
        "candidate_approved": result.candidate_approved,
        "baseline_metrics": result.baseline_metrics,
        "candidate_metrics": result.candidate_metrics,
        "notes": result.approval_notes,
    }
    report.artifact = Model2Artifact(feature_names=feature_names, head=result.head)
    return report
