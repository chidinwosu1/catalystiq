"""Matplotlib plot builders for MLflow artifacts (optional dependency).

Every function returns a matplotlib ``Figure`` or ``None`` when matplotlib is
not installed, so the training runner logs plots when it can and silently
falls back to the equivalent JSON artifact when it cannot. Nothing here fits a
model or touches held-out data - the callers pass already-computed evaluation
arrays.

The plots implement the spec's artifact list: calibration/reliability diagram,
confusion matrix, ROC curve, precision-recall curve, predicted-vs-actual,
quantile-coverage, and feature importance.
"""
from __future__ import annotations

from typing import Mapping, Sequence

import numpy as np


def matplotlib_available() -> bool:
    try:
        import matplotlib  # noqa: F401
        return True
    except Exception:
        return False


def _new_ax(title: str, xlabel: str, ylabel: str):
    import matplotlib
    matplotlib.use("Agg")  # headless
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(5.0, 4.0))
    ax.set_title(title)
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    return fig, ax


def calibration_plot(reliability_bins, *, title: str):
    """Reliability diagram from :class:`ReliabilityBin` rows (mean predicted vs
    empirical rate), with the ideal y=x diagonal."""
    if not matplotlib_available():
        return None
    fig, ax = _new_ax(title, "Mean predicted probability", "Empirical frequency")
    xs = [b.mean_predicted for b in reliability_bins if b.count > 0]
    ys = [b.empirical_rate for b in reliability_bins if b.count > 0]
    ax.plot([0, 1], [0, 1], linestyle="--", linewidth=1, color="gray", label="Perfectly calibrated")
    if xs:
        ax.plot(xs, ys, marker="o", linewidth=1.5, label="Model")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.legend(loc="best", fontsize=8)
    fig.tight_layout()
    return fig


def roc_curve_plot(y_true: Sequence[int], y_prob: Sequence[float], *, title: str):
    if not matplotlib_available():
        return None
    fpr, tpr = _roc_points(np.asarray(y_true, float), np.asarray(y_prob, float))
    if fpr is None:
        return None
    fig, ax = _new_ax(title, "False positive rate", "True positive rate")
    ax.plot([0, 1], [0, 1], linestyle="--", linewidth=1, color="gray")
    ax.plot(fpr, tpr, linewidth=1.5)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    fig.tight_layout()
    return fig


def pr_curve_plot(y_true: Sequence[int], y_prob: Sequence[float], *, title: str):
    if not matplotlib_available():
        return None
    rec, prec = _pr_points(np.asarray(y_true, float), np.asarray(y_prob, float))
    if rec is None:
        return None
    fig, ax = _new_ax(title, "Recall", "Precision")
    ax.plot(rec, prec, linewidth=1.5)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1.02)
    fig.tight_layout()
    return fig


def confusion_matrix_plot(y_true: Sequence[int], y_prob: Sequence[float], *, threshold: float = 0.5, title: str):
    if not matplotlib_available():
        return None
    y = np.asarray(y_true, float)
    pred = (np.asarray(y_prob, float) >= threshold).astype(float)
    tp = float(((pred == 1) & (y == 1)).sum())
    fp = float(((pred == 1) & (y == 0)).sum())
    fn = float(((pred == 0) & (y == 1)).sum())
    tn = float(((pred == 0) & (y == 0)).sum())
    mat = np.array([[tn, fp], [fn, tp]])
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(4.0, 3.6))
    ax.imshow(mat, cmap="Blues")
    ax.set_title(title)
    ax.set_xticks([0, 1], labels=["pred 0", "pred 1"])
    ax.set_yticks([0, 1], labels=["true 0", "true 1"])
    for i in range(2):
        for j in range(2):
            ax.text(j, i, int(mat[i, j]), ha="center", va="center", color="black")
    fig.tight_layout()
    return fig


def predicted_vs_actual_plot(y_true: Sequence[float], y_pred: Sequence[float], *, title: str):
    if not matplotlib_available():
        return None
    y = np.asarray(y_true, float)
    p = np.asarray(y_pred, float)
    if y.size == 0:
        return None
    fig, ax = _new_ax(title, "Predicted", "Actual")
    ax.scatter(p, y, s=8, alpha=0.4)
    lo = float(min(p.min(), y.min()))
    hi = float(max(p.max(), y.max()))
    ax.plot([lo, hi], [lo, hi], linestyle="--", linewidth=1, color="gray")
    fig.tight_layout()
    return fig


def quantile_coverage_plot(coverage: Mapping[str, float], *, title: str):
    """Nominal vs empirical coverage per predicted quantile."""
    if not matplotlib_available():
        return None
    items = sorted(
        ((float(k[1:]) / 100.0, v) for k, v in coverage.items() if isinstance(v, (int, float))),
        key=lambda t: t[0],
    )
    if not items:
        return None
    nominal = [t[0] for t in items]
    empirical = [t[1] for t in items]
    fig, ax = _new_ax(title, "Nominal quantile", "Empirical coverage")
    ax.plot([0, 1], [0, 1], linestyle="--", linewidth=1, color="gray", label="Ideal")
    ax.plot(nominal, empirical, marker="o", linewidth=1.5, label="Observed")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.legend(loc="best", fontsize=8)
    fig.tight_layout()
    return fig


def feature_importance_plot(names: Sequence[str], importances: Sequence[float], *, title: str, top_n: int = 20):
    if not matplotlib_available():
        return None
    pairs = sorted(zip(names, importances), key=lambda t: abs(t[1]), reverse=True)[:top_n]
    if not pairs:
        return None
    labels = [p[0] for p in pairs][::-1]
    vals = [p[1] for p in pairs][::-1]
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(6.0, max(3.0, 0.3 * len(labels))))
    ax.barh(range(len(labels)), vals)
    ax.set_yticks(range(len(labels)), labels=labels, fontsize=7)
    ax.set_title(title)
    ax.set_xlabel("Importance")
    fig.tight_layout()
    return fig


# --- curve math (pure numpy) -----------------------------------------------
def _roc_points(y: np.ndarray, p: np.ndarray):
    pos = int((y == 1).sum())
    neg = int((y == 0).sum())
    if pos == 0 or neg == 0:
        return None, None
    order = np.argsort(-p, kind="mergesort")
    ys = y[order]
    tp = np.cumsum(ys == 1)
    fp = np.cumsum(ys == 0)
    tpr = np.concatenate([[0.0], tp / pos])
    fpr = np.concatenate([[0.0], fp / neg])
    return fpr, tpr


def _pr_points(y: np.ndarray, p: np.ndarray):
    pos = int((y == 1).sum())
    if pos == 0:
        return None, None
    order = np.argsort(-p, kind="mergesort")
    ys = y[order]
    tp = np.cumsum(ys == 1)
    fp = np.cumsum(ys == 0)
    recall = tp / pos
    precision = tp / np.maximum(tp + fp, 1)
    return recall, precision
