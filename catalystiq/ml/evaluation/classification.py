"""Classification metrics (pure numpy - no sklearn dependency).

Every metric the ML spec requires for the calibrated classifiers:
ROC-AUC, PR-AUC, precision, recall, F1, Brier score, log loss and expected
calibration error. Implemented directly so they run in any environment and so
their behaviour is transparent and testable.
"""
from __future__ import annotations

from typing import Sequence

import numpy as np

from catalystiq.ml.calibration import expected_calibration_error


def _arrays(y_true: Sequence[int], y_prob: Sequence[float]) -> tuple[np.ndarray, np.ndarray]:
    y = np.asarray(y_true, dtype=float)
    p = np.clip(np.asarray(y_prob, dtype=float), 0.0, 1.0)
    if y.shape != p.shape:
        raise ValueError("y_true and y_prob must be the same length")
    return y, p


def roc_auc(y_true: Sequence[int], y_prob: Sequence[float]) -> float:
    """ROC-AUC via the Mann-Whitney U statistic (handles ties)."""
    y, p = _arrays(y_true, y_prob)
    pos = p[y == 1]
    neg = p[y == 0]
    n_pos, n_neg = pos.size, neg.size
    if n_pos == 0 or n_neg == 0:
        return float("nan")
    order = np.argsort(p, kind="mergesort")
    ranks = np.empty(p.size, dtype=float)
    sorted_p = p[order]
    # Average ranks for ties.
    i = 0
    rank = 1
    while i < sorted_p.size:
        j = i
        while j + 1 < sorted_p.size and sorted_p[j + 1] == sorted_p[i]:
            j += 1
        avg = (rank + (rank + (j - i))) / 2.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        rank += (j - i) + 1
        i = j + 1
    sum_ranks_pos = ranks[y == 1].sum()
    auc = (sum_ranks_pos - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg)
    return float(auc)


def pr_auc(y_true: Sequence[int], y_prob: Sequence[float]) -> float:
    """Average precision (area under precision-recall), the standard PR-AUC."""
    y, p = _arrays(y_true, y_prob)
    n_pos = int((y == 1).sum())
    if n_pos == 0:
        return float("nan")
    order = np.argsort(-p, kind="mergesort")
    y_sorted = y[order]
    tp = 0
    fp = 0
    prev_recall = 0.0
    ap = 0.0
    for i, yi in enumerate(y_sorted):
        if yi == 1:
            tp += 1
        else:
            fp += 1
        precision = tp / (tp + fp)
        recall = tp / n_pos
        ap += precision * (recall - prev_recall)
        prev_recall = recall
    return float(ap)


def precision_recall_f1(
    y_true: Sequence[int], y_prob: Sequence[float], *, threshold: float = 0.5
) -> tuple[float, float, float]:
    y, p = _arrays(y_true, y_prob)
    pred = (p >= threshold).astype(float)
    tp = float(((pred == 1) & (y == 1)).sum())
    fp = float(((pred == 1) & (y == 0)).sum())
    fn = float(((pred == 0) & (y == 1)).sum())
    precision = tp / (tp + fp) if (tp + fp) > 0 else float("nan")
    recall = tp / (tp + fn) if (tp + fn) > 0 else float("nan")
    if precision != precision or recall != recall or (precision + recall) == 0:
        f1 = float("nan")
    else:
        f1 = 2 * precision * recall / (precision + recall)
    return precision, recall, f1


def brier_score(y_true: Sequence[int], y_prob: Sequence[float]) -> float:
    y, p = _arrays(y_true, y_prob)
    if y.size == 0:
        return float("nan")
    return float(np.mean((p - y) ** 2))


def log_loss(y_true: Sequence[int], y_prob: Sequence[float], *, eps: float = 1e-12) -> float:
    y, p = _arrays(y_true, y_prob)
    if y.size == 0:
        return float("nan")
    p = np.clip(p, eps, 1 - eps)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def classification_metrics(
    y_true: Sequence[int],
    y_prob: Sequence[float],
    *,
    threshold: float = 0.5,
    n_calibration_bins: int = 10,
) -> dict[str, float]:
    """Full metric bundle for one calibrated classifier head."""
    precision, recall, f1 = precision_recall_f1(y_true, y_prob, threshold=threshold)
    return {
        "roc_auc": roc_auc(y_true, y_prob),
        "pr_auc": pr_auc(y_true, y_prob),
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "brier_score": brier_score(y_true, y_prob),
        "log_loss": log_loss(y_true, y_prob),
        "expected_calibration_error": expected_calibration_error(
            y_prob, y_true, n_bins=n_calibration_bins
        ),
        "n": float(len(y_true)),
        "positive_rate": float(np.mean(np.asarray(y_true, dtype=float))) if len(y_true) else float("nan"),
    }
