"""Quantile-regression metrics (Models 2 and 3).

Pinball (quantile) loss per quantile, MAE for the median, empirical coverage
per predicted quantile, and quantile-crossing detection. Coverage is the
fraction of realized outcomes at or below each predicted quantile - a
well-calibrated q10 should cover ~10%, q90 ~90%.
"""
from __future__ import annotations

from typing import Mapping, Sequence

import numpy as np


def pinball_loss(y_true: Sequence[float], y_pred: Sequence[float], q: float) -> float:
    y = np.asarray(y_true, dtype=float)
    f = np.asarray(y_pred, dtype=float)
    if y.size == 0:
        return float("nan")
    diff = y - f
    return float(np.mean(np.maximum(q * diff, (q - 1) * diff)))


def empirical_coverage(y_true: Sequence[float], y_pred_quantile: Sequence[float]) -> float:
    """Fraction of realized values <= the predicted quantile."""
    y = np.asarray(y_true, dtype=float)
    f = np.asarray(y_pred_quantile, dtype=float)
    if y.size == 0:
        return float("nan")
    return float(np.mean(y <= f))


def quantile_crossing_detected(quantiles: Mapping[str, float]) -> bool:
    """True if the predicted quantiles violate monotonic ordering.

    Expects keys like ``q10``,``q25``,``q50``,``q75``,``q90``. Only the keys
    present are checked, in ascending numeric order.
    """
    ordered = _ordered_quantile_values(quantiles)
    for a, b in zip(ordered, ordered[1:]):
        if a > b + 1e-12:
            return True
    return False


def _ordered_quantile_values(quantiles: Mapping[str, float]) -> list[float]:
    def level(k: str) -> float:
        return float(k[1:]) if k.lower().startswith("q") else float(k)

    items = sorted(((level(k), v) for k, v in quantiles.items()), key=lambda t: t[0])
    return [v for _, v in items]


def quantile_metrics(
    y_true: Sequence[float],
    predicted: Mapping[str, Sequence[float]],
    *,
    median_key: str = "q50",
) -> dict:
    """Per-quantile pinball loss + coverage, plus median MAE.

    ``predicted`` maps quantile key -> per-sample predictions.
    """
    y = np.asarray(y_true, dtype=float)
    out: dict = {"pinball_loss": {}, "coverage": {}}
    for key, preds in predicted.items():
        q = float(key[1:]) / 100.0 if key.lower().startswith("q") else float(key)
        out["pinball_loss"][key] = pinball_loss(y, preds, q)
        out["coverage"][key] = empirical_coverage(y, preds)
    if median_key in predicted and y.size:
        med = np.asarray(predicted[median_key], dtype=float)
        out["median_mae"] = float(np.mean(np.abs(y - med)))
    else:
        out["median_mae"] = float("nan")
    out["n"] = float(y.size)
    return out
