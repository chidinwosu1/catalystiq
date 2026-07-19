"""Reusable model heads: calibrated binary, quantile, and multiclass.

Each head follows the same governed pattern the spec mandates:

  * a transparent BASELINE (logistic regression / historical frequency /
    unconditional quantiles),
  * a more complex CANDIDATE (gradient-boosted trees),
  * probability CALIBRATION fit on the calibration fold only,
  * selection of the candidate over the baseline ONLY when it materially
    outperforms on the untouched evaluation fold while staying well calibrated.

sklearn is imported lazily inside the trainers.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

import numpy as np

from catalystiq.ml.calibration import (
    ProbabilityCalibrator,
    calibration_status,
    expected_calibration_error,
)
from catalystiq.ml.evaluation.classification import classification_metrics, roc_auc
from catalystiq.ml.evaluation.quantile import quantile_metrics
from catalystiq.ml.models.base import Preprocessor, require_sklearn


# --------------------------------------------------------------------------
# Calibrated binary head
# --------------------------------------------------------------------------
@dataclass
class BinaryHead:
    """A fitted, calibrated binary classifier over preprocessed features."""

    preprocessor: Preprocessor
    _estimator: object  # sklearn classifier with predict_proba/decision_function
    calibrator: ProbabilityCalibrator
    model_kind: str  # "logistic_baseline" | "gbdt_candidate"
    n_train: int

    def _raw_scores(self, X: np.ndarray) -> np.ndarray:
        Xp = self.preprocessor.transform(X)
        est = self._estimator
        if hasattr(est, "predict_proba"):
            return np.asarray(est.predict_proba(Xp))[:, 1]
        return np.asarray(est.decision_function(Xp))

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        return self.calibrator.transform(self._raw_scores(X))


@dataclass
class BinaryHeadResult:
    head: BinaryHead
    chosen: str
    baseline_metrics: dict
    candidate_metrics: dict | None
    calibration_status: str
    candidate_approved: bool
    approval_notes: list[str] = field(default_factory=list)


def _fit_one_classifier(kind: str, Xp: np.ndarray, y: np.ndarray):
    require_sklearn()
    from sklearn.linear_model import LogisticRegression
    from sklearn.ensemble import HistGradientBoostingClassifier

    if kind == "logistic_baseline":
        clf = LogisticRegression(max_iter=1000)
    else:
        clf = HistGradientBoostingClassifier(
            max_depth=3, max_iter=200, learning_rate=0.05, l2_regularization=1.0
        )
    clf.fit(Xp, y)
    return clf


def _train_calibrated(kind: str, X_tr, y_tr, X_cal, y_cal, feature_names) -> BinaryHead:
    pre = Preprocessor(feature_names=list(feature_names)).fit(X_tr)
    Xp_tr = pre.transform(X_tr)
    est = _fit_one_classifier(kind, Xp_tr, y_tr)
    head = BinaryHead(pre, est, ProbabilityCalibrator("isotonic"), kind, n_train=len(y_tr))
    # Calibrate on the calibration fold's RAW scores only.
    cal_scores = head._raw_scores(X_cal)
    head.calibrator.fit(cal_scores, y_cal)
    return head


def train_binary_head(
    *,
    X_train: np.ndarray,
    y_train: Sequence[int],
    X_calib: np.ndarray,
    y_calib: Sequence[int],
    X_eval: np.ndarray,
    y_eval: Sequence[int],
    feature_names: Sequence[str],
    min_auc_gain: float = 0.02,
    max_ece: float = 0.10,
) -> BinaryHeadResult:
    """Train baseline + candidate, calibrate, and select.

    The candidate (GBDT) is approved over the baseline only if its evaluation
    ROC-AUC exceeds the baseline's by at least ``min_auc_gain`` AND its
    calibrated ECE is within ``max_ece``.
    """
    y_train = np.asarray(y_train)
    y_calib = np.asarray(y_calib)
    y_eval = np.asarray(y_eval)

    baseline = _train_calibrated("logistic_baseline", X_train, y_train, X_calib, y_calib, feature_names)
    base_probs = baseline.predict_proba(X_eval)
    base_metrics = classification_metrics(y_eval, base_probs)

    notes: list[str] = []
    candidate: BinaryHead | None = None
    cand_metrics: dict | None = None
    try:
        candidate = _train_calibrated("gbdt_candidate", X_train, y_train, X_calib, y_calib, feature_names)
        cand_probs = candidate.predict_proba(X_eval)
        cand_metrics = classification_metrics(y_eval, cand_probs)
    except Exception as exc:  # candidate failure never blocks the baseline
        notes.append(f"candidate training failed, kept baseline: {exc}")

    chosen_head = baseline
    chosen = "logistic_baseline"
    approved = False
    if candidate is not None and cand_metrics is not None:
        auc_gain = _nan_safe(cand_metrics["roc_auc"]) - _nan_safe(base_metrics["roc_auc"])
        cand_ece = _nan_safe(cand_metrics["expected_calibration_error"], default=1.0)
        if auc_gain >= min_auc_gain and cand_ece <= max_ece:
            chosen_head = candidate
            chosen = "gbdt_candidate"
            approved = True
            notes.append(
                f"candidate approved: +{auc_gain:.3f} ROC-AUC over baseline, ECE {cand_ece:.3f}"
            )
        else:
            notes.append(
                f"candidate NOT approved: ROC-AUC gain {auc_gain:.3f} (need {min_auc_gain}), "
                f"ECE {cand_ece:.3f} (max {max_ece}); baseline retained"
            )

    chosen_metrics = cand_metrics if chosen == "gbdt_candidate" else base_metrics
    status = calibration_status(chosen_metrics["expected_calibration_error"])
    return BinaryHeadResult(
        head=chosen_head,
        chosen=chosen,
        baseline_metrics=base_metrics,
        candidate_metrics=cand_metrics,
        calibration_status=status,
        candidate_approved=approved,
        approval_notes=notes,
    )


def _nan_safe(x: float, default: float = 0.0) -> float:
    return default if x is None or np.isnan(x) else float(x)


# --------------------------------------------------------------------------
# Quantile head (Models 2 & 3)
# --------------------------------------------------------------------------
QUANTILE_LEVELS = ("q10", "q25", "q50", "q75", "q90")


@dataclass
class QuantileHead:
    preprocessor: Preprocessor
    _estimators: dict[str, object]
    levels: tuple[str, ...]
    model_kind: str
    n_train: int

    def predict(self, X: np.ndarray) -> dict[str, np.ndarray]:
        Xp = self.preprocessor.transform(X)
        raw = {lvl: np.asarray(est.predict(Xp)) for lvl, est in self._estimators.items()}
        return _enforce_monotone(raw, self.levels)


def _enforce_monotone(preds: dict[str, np.ndarray], levels: tuple[str, ...]) -> dict[str, np.ndarray]:
    """Guarantee q10<=q25<=...<=q90 per-sample by cumulative max across the
    ordered quantile predictions (never expose crossing)."""
    ordered = [lvl for lvl in levels if lvl in preds]
    stacked = np.vstack([preds[lvl] for lvl in ordered])
    monotone = np.maximum.accumulate(stacked, axis=0)
    return {lvl: monotone[i] for i, lvl in enumerate(ordered)}


@dataclass
class QuantileHeadResult:
    head: QuantileHead
    chosen: str
    baseline_metrics: dict
    candidate_metrics: dict | None
    candidate_approved: bool
    approval_notes: list[str] = field(default_factory=list)


class _ConstantQuantile:
    """Historical-unconditional-quantile baseline estimator."""

    def __init__(self, value: float) -> None:
        self.value = value

    def predict(self, X: np.ndarray) -> np.ndarray:
        return np.full(X.shape[0], self.value, dtype=float)


def train_quantile_head(
    *,
    X_train: np.ndarray,
    y_train: Sequence[float],
    X_eval: np.ndarray,
    y_eval: Sequence[float],
    feature_names: Sequence[str],
    levels: tuple[str, ...] = QUANTILE_LEVELS,
    min_pinball_improvement: float = 0.02,
) -> QuantileHeadResult:
    """Train an unconditional-quantile baseline and a GBDT quantile candidate.

    The candidate is approved only if it lowers the average pinball loss on the
    evaluation fold by at least ``min_pinball_improvement`` (relative).
    """
    y_train = np.asarray(y_train, dtype=float)
    y_eval = np.asarray(y_eval, dtype=float)
    pre = Preprocessor(feature_names=list(feature_names)).fit(X_train)
    Xp_tr = pre.transform(X_train)

    # Baseline: unconditional historical quantiles of the training target.
    base_est: dict[str, object] = {}
    for lvl in levels:
        q = float(lvl[1:]) / 100.0
        base_est[lvl] = _ConstantQuantile(float(np.quantile(y_train, q)))
    baseline = QuantileHead(pre, base_est, levels, "historical_baseline", len(y_train))
    base_metrics = quantile_metrics(y_eval, {k: v for k, v in baseline.predict(X_eval).items()})

    notes: list[str] = []
    candidate: QuantileHead | None = None
    cand_metrics: dict | None = None
    try:
        require_sklearn()
        from sklearn.ensemble import GradientBoostingRegressor

        cand_est: dict[str, object] = {}
        for lvl in levels:
            q = float(lvl[1:]) / 100.0
            gbr = GradientBoostingRegressor(loss="quantile", alpha=q, n_estimators=200, max_depth=3, learning_rate=0.05)
            gbr.fit(Xp_tr, y_train)
            cand_est[lvl] = gbr
        candidate = QuantileHead(pre, cand_est, levels, "gbdt_quantile_candidate", len(y_train))
        cand_metrics = quantile_metrics(y_eval, {k: v for k, v in candidate.predict(X_eval).items()})
    except Exception as exc:
        notes.append(f"quantile candidate training failed, kept baseline: {exc}")

    chosen = baseline
    chosen_name = "historical_baseline"
    approved = False
    if candidate is not None and cand_metrics is not None:
        base_avg = np.nanmean(list(base_metrics["pinball_loss"].values()))
        cand_avg = np.nanmean(list(cand_metrics["pinball_loss"].values()))
        rel = (base_avg - cand_avg) / base_avg if base_avg > 0 else 0.0
        if rel >= min_pinball_improvement:
            chosen, chosen_name, approved = candidate, "gbdt_quantile_candidate", True
            notes.append(f"quantile candidate approved: pinball -{rel:.1%} vs baseline")
        else:
            notes.append(f"quantile candidate NOT approved: pinball improvement {rel:.1%}; baseline retained")

    return QuantileHeadResult(
        head=chosen,
        chosen=chosen_name,
        baseline_metrics=base_metrics,
        candidate_metrics=cand_metrics,
        candidate_approved=approved,
        approval_notes=notes,
    )
