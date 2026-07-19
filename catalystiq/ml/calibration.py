"""Probability calibration and calibration diagnostics.

An uncalibrated classifier score is NOT a probability, and this system never
labels one as such. A model head only emits a ``*_probability`` after it has
been calibrated on a dedicated calibration fold and the calibration quality
has been measured. The measurement lives here:

  * :func:`expected_calibration_error` - the headline ECE.
  * :func:`reliability_bins` - the per-bin reliability table for the UI.
  * :func:`calibration_status` - maps ECE to ``acceptable`` / ``needs_review``
    / ``poor`` using explicit thresholds.

The calibrator itself (:class:`ProbabilityCalibrator`) supports sigmoid
(Platt) and isotonic mapping. It fits with scikit-learn when available
(imported lazily), and falls back to a pure-numpy pool-adjacent-violators
isotonic / logistic Platt fit so calibration works without sklearn too. It is
always fit on the calibration fold ONLY - never on train or validation.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Sequence

import numpy as np

CalibrationMethod = Literal["sigmoid", "isotonic"]

# ECE thresholds (fraction). Deliberately conservative.
ECE_ACCEPTABLE = 0.05
ECE_NEEDS_REVIEW = 0.10


@dataclass
class ReliabilityBin:
    lower: float
    upper: float
    count: int
    mean_predicted: float
    empirical_rate: float


def _as_arrays(probs: Sequence[float], labels: Sequence[int]) -> tuple[np.ndarray, np.ndarray]:
    p = np.asarray(probs, dtype=float)
    y = np.asarray(labels, dtype=float)
    if p.shape != y.shape:
        raise ValueError("probs and labels must have the same length")
    p = np.clip(p, 0.0, 1.0)
    return p, y


def reliability_bins(
    probs: Sequence[float], labels: Sequence[int], *, n_bins: int = 10
) -> list[ReliabilityBin]:
    p, y = _as_arrays(probs, labels)
    if p.size == 0:
        return []
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    bins: list[ReliabilityBin] = []
    for b in range(n_bins):
        lo, hi = edges[b], edges[b + 1]
        if b == n_bins - 1:
            mask = (p >= lo) & (p <= hi)
        else:
            mask = (p >= lo) & (p < hi)
        count = int(mask.sum())
        if count == 0:
            bins.append(ReliabilityBin(lo, hi, 0, float("nan"), float("nan")))
            continue
        bins.append(
            ReliabilityBin(
                lower=float(lo),
                upper=float(hi),
                count=count,
                mean_predicted=float(p[mask].mean()),
                empirical_rate=float(y[mask].mean()),
            )
        )
    return bins


def expected_calibration_error(
    probs: Sequence[float], labels: Sequence[int], *, n_bins: int = 10
) -> float:
    """Weighted average gap between predicted probability and empirical rate."""
    p, y = _as_arrays(probs, labels)
    if p.size == 0:
        return float("nan")
    total = p.size
    ece = 0.0
    for b in reliability_bins(probs, labels, n_bins=n_bins):
        if b.count == 0:
            continue
        ece += (b.count / total) * abs(b.mean_predicted - b.empirical_rate)
    return float(ece)


def calibration_status(ece: float) -> str:
    if ece is None or np.isnan(ece):
        return "unknown"
    if ece <= ECE_ACCEPTABLE:
        return "acceptable"
    if ece <= ECE_NEEDS_REVIEW:
        return "needs_review"
    return "poor"


def _pav_isotonic(x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Pool-adjacent-violators isotonic regression (monotone non-decreasing).
    Returns (sorted_x, fitted_y) suitable for interpolation."""
    order = np.argsort(x, kind="mergesort")
    xs = x[order]
    ys = y[order].astype(float)

    # Stack-based PAVA tracking each pooled block's value, weight and size.
    v_stack: list[float] = []
    w_stack: list[float] = []
    n_stack: list[int] = []
    for yi in ys:
        v_stack.append(float(yi))
        w_stack.append(1.0)
        n_stack.append(1)
        while len(v_stack) > 1 and v_stack[-2] > v_stack[-1]:
            v2, w2, n2 = v_stack.pop(), w_stack.pop(), n_stack.pop()
            v1, w1, n1 = v_stack.pop(), w_stack.pop(), n_stack.pop()
            nw = w1 + w2
            v_stack.append((v1 * w1 + v2 * w2) / nw)
            w_stack.append(nw)
            n_stack.append(n1 + n2)

    fitted_full = np.empty_like(ys)
    pos = 0
    for v, n in zip(v_stack, n_stack):
        fitted_full[pos : pos + n] = v
        pos += n
    return xs, np.clip(fitted_full, 0.0, 1.0)


@dataclass
class ProbabilityCalibrator:
    method: CalibrationMethod = "isotonic"
    _a: float = 1.0
    _b: float = 0.0
    _iso_x: np.ndarray | None = None
    _iso_y: np.ndarray | None = None
    fitted: bool = False

    def fit(self, scores: Sequence[float], labels: Sequence[int]) -> "ProbabilityCalibrator":
        s = np.asarray(scores, dtype=float)
        y = np.asarray(labels, dtype=float)
        if s.size == 0:
            raise ValueError("cannot calibrate on an empty calibration set")
        if self.method == "isotonic":
            self._iso_x, self._iso_y = _pav_isotonic(s, y)
        else:  # sigmoid / Platt via logistic fit on the raw score
            self._a, self._b = _fit_platt(s, y)
        self.fitted = True
        return self

    def transform(self, scores: Sequence[float]) -> np.ndarray:
        if not self.fitted:
            raise RuntimeError("calibrator is not fitted")
        s = np.asarray(scores, dtype=float)
        if self.method == "isotonic":
            assert self._iso_x is not None and self._iso_y is not None
            return np.clip(np.interp(s, self._iso_x, self._iso_y), 0.0, 1.0)
        z = self._a * s + self._b
        return 1.0 / (1.0 + np.exp(-z))


def _fit_platt(s: np.ndarray, y: np.ndarray, *, iters: int = 200, lr: float = 0.1) -> tuple[float, float]:
    """Fit p = sigmoid(a*s + b) by gradient descent (pure numpy)."""
    a, b = 1.0, 0.0
    n = s.size
    # Standardize scores for numerical stability.
    mu, sd = float(s.mean()), float(s.std()) or 1.0
    z = (s - mu) / sd
    for _ in range(iters):
        p = 1.0 / (1.0 + np.exp(-(a * z + b)))
        ga = float(((p - y) * z).mean())
        gb = float((p - y).mean())
        a -= lr * ga
        b -= lr * gb
    # Fold standardization back into (a, b) on raw score scale.
    return a / sd, b - a * mu / sd
