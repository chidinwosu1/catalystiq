"""Model 3 - Path-risk & tail-loss model.

Evaluates the PATH between entry and exit, not just the terminal return:

  * adverse-excursion quantiles  -> median & severe (q10) adverse excursion
  * favorable-excursion quantile -> median favorable excursion
  * terminal-return lower tail   -> severe terminal return (q10)
  * stop-breach probability       (calibrated classifier)
  * gap-beyond-stop probability   (calibrated classifier)

Gap events are rare. This model NEVER reports a confident zero gap-risk merely
because gaps are rare: when the holdout has too few gap events to estimate a
calibrated probability, the gap output is ``insufficient_evidence``.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from catalystiq.ml.dataset.builder import TrainingDataset, TrainingExample
from catalystiq.ml.models.base import to_matrix
from catalystiq.ml.models.heads import (
    BinaryHead,
    QuantileHead,
    train_binary_head,
    train_quantile_head,
)
from catalystiq.ml.models.training import (
    SplitReport,
    chronological_split,
    matrices_for,
    stable_feature_names,
)

INSUFFICIENT = "insufficient_evidence"
MIN_RARE_EVENTS = 20  # minimum positive events to estimate a rare-event prob


@dataclass
class Model3Prediction:
    median_adverse_excursion: float | None
    severe_adverse_excursion: float | None
    median_favorable_excursion: float | None
    stop_breach_probability: float | str
    gap_beyond_stop_probability: float | str
    severe_terminal_return: float | None


@dataclass
class Model3Artifact:
    feature_names: list[str]
    mae_head: QuantileHead
    mfe_head: QuantileHead
    tail_return_head: QuantileHead
    stop_breach_head: BinaryHead | None
    gap_head: BinaryHead | None
    gap_evidence_ok: bool

    def predict(self, features: dict) -> Model3Prediction:
        X = to_matrix([features], self.feature_names)
        mae = self.mae_head.predict(X)
        mfe = self.mfe_head.predict(X)
        tail = self.tail_return_head.predict(X)
        stop_p: float | str = (
            float(self.stop_breach_head.predict_proba(X)[0]) if self.stop_breach_head else INSUFFICIENT
        )
        if self.gap_head is not None and self.gap_evidence_ok:
            gap_p: float | str = float(self.gap_head.predict_proba(X)[0])
        else:
            gap_p = INSUFFICIENT
        return Model3Prediction(
            median_adverse_excursion=float(mae["q50"][0]) if "q50" in mae else None,
            severe_adverse_excursion=float(mae["q10"][0]) if "q10" in mae else None,
            median_favorable_excursion=float(mfe["q50"][0]) if "q50" in mfe else None,
            stop_breach_probability=stop_p,
            gap_beyond_stop_probability=gap_p,
            severe_terminal_return=float(tail["q10"][0]) if "q10" in tail else None,
        )


@dataclass
class Model3TrainingReport:
    horizon_days: int
    direction: str
    split: SplitReport
    metrics: dict
    artifact: Model3Artifact | None = None
    warnings: list[str] = field(default_factory=list)


def train_model_three(
    dataset: TrainingDataset, *, horizon_days: int, direction: str = "long"
) -> Model3TrainingReport:
    feature_names = stable_feature_names(dataset)
    split = chronological_split(dataset)
    report = Model3TrainingReport(
        horizon_days=horizon_days, direction=direction,
        split=SplitReport.from_split(dataset, split), metrics={},
    )
    if not split.train_idx or not split.holdout_idx or not split.calib_idx:
        report.warnings.append("insufficient chronological data for path-risk training")
        return report

    def q_head(getter):
        X_tr, y_tr, _ = matrices_for(dataset, feature_names, split.train_idx, getter)
        X_ho, y_ho, _ = matrices_for(dataset, feature_names, split.holdout_idx, getter)
        if len(y_tr) < 30:
            return None, {}
        res = train_quantile_head(
            X_train=X_tr, y_train=y_tr, X_eval=X_ho, y_eval=y_ho, feature_names=feature_names
        )
        return res.head, {"chosen": res.chosen, "candidate_approved": res.candidate_approved,
                          "candidate_metrics": res.candidate_metrics}

    mae_head, mae_m = q_head(lambda ex: ex.labels.max_adverse_excursion)
    mfe_head, mfe_m = q_head(lambda ex: ex.labels.max_favorable_excursion)
    tail_head, tail_m = q_head(lambda ex: ex.labels.net_terminal_return)
    if mae_head is None or mfe_head is None or tail_head is None:
        report.warnings.append("too few excursion/return examples for quantile heads")
        return report

    stop_head, stop_m = _binary_head(dataset, feature_names, split, lambda ex: ex.labels.stop_breach_label)
    gap_head, gap_m, gap_ok = _rare_binary_head(
        dataset, feature_names, split, lambda ex: ex.labels.gap_beyond_stop_label
    )
    if not gap_ok:
        report.warnings.append(
            f"gap-beyond-stop events below {MIN_RARE_EVENTS} in holdout - reporting "
            "insufficient_evidence rather than a zero gap-risk estimate"
        )

    report.metrics = {
        "adverse_excursion": mae_m,
        "favorable_excursion": mfe_m,
        "tail_return": tail_m,
        "stop_breach": stop_m,
        "gap_beyond_stop": gap_m,
        "gap_evidence_ok": gap_ok,
    }
    report.artifact = Model3Artifact(
        feature_names=feature_names,
        mae_head=mae_head, mfe_head=mfe_head, tail_return_head=tail_head,
        stop_breach_head=stop_head, gap_head=gap_head, gap_evidence_ok=gap_ok,
    )
    return report


def _binary_head(dataset, feature_names, split, getter):
    X_tr, y_tr, _ = matrices_for(dataset, feature_names, split.train_idx, getter)
    X_cal, y_cal, _ = matrices_for(dataset, feature_names, split.calib_idx, getter)
    X_ho, y_ho, _ = matrices_for(dataset, feature_names, split.holdout_idx, getter)
    if len(np.unique(y_tr)) < 2 or len(np.unique(y_cal)) < 2 or len(y_ho) == 0:
        return None, {"status": "not_trained"}
    res = train_binary_head(
        X_train=X_tr, y_train=y_tr, X_calib=X_cal, y_calib=y_cal,
        X_eval=X_ho, y_eval=y_ho, feature_names=feature_names,
    )
    return res.head, {"chosen": res.chosen, "candidate_approved": res.candidate_approved,
                      "calibration_status": res.calibration_status,
                      "metrics": res.candidate_metrics or res.baseline_metrics}


def _rare_binary_head(dataset, feature_names, split, getter):
    """Like _binary_head but returns evidence_ok=False when the holdout has too
    few positive (rare) events to justify a calibrated estimate."""
    _, y_ho, _ = matrices_for(dataset, feature_names, split.holdout_idx, getter)
    positives = int(np.sum(y_ho == 1)) if len(y_ho) else 0
    head, metrics = _binary_head(dataset, feature_names, split, getter)
    evidence_ok = head is not None and positives >= MIN_RARE_EVENTS
    metrics = {**metrics, "holdout_positive_events": positives}
    return head, metrics, evidence_ok
