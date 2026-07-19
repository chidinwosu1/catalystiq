"""Shared model plumbing: train-only preprocessing and sklearn gating.

The cardinal validation rule is that ALL preprocessing - imputation, scaling,
winsorization, feature selection, hyperparameter tuning - is fit on the
TRAINING fold only. The calibration, validation and holdout folds never
influence a fitted statistic. :class:`Preprocessor` enforces that: it stores
the statistics learned from ``fit`` and applies them unchanged in
``transform``.

scikit-learn is imported lazily via :func:`require_sklearn` so this module
imports without it. Training raises a clear error when sklearn is absent
rather than at import time.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

import numpy as np


class SklearnUnavailableError(RuntimeError):
    """scikit-learn is required for training but is not installed."""


def require_sklearn():
    try:
        import sklearn  # noqa: F401
    except Exception as exc:  # pragma: no cover - environment dependent
        raise SklearnUnavailableError(
            "scikit-learn is required for ML training. Install it "
            "(it is in requirements.txt) before training candidate models."
        ) from exc
    return sklearn


def sklearn_available() -> bool:
    try:
        import sklearn  # noqa: F401

        return True
    except Exception:  # pragma: no cover
        return False


def to_matrix(
    rows: Sequence[dict], feature_names: Sequence[str]
) -> np.ndarray:
    """Stack feature dicts into a float matrix, missing -> NaN (imputed later)."""
    out = np.full((len(rows), len(feature_names)), np.nan, dtype=float)
    for i, row in enumerate(rows):
        for j, name in enumerate(feature_names):
            v = row.get(name)
            if v is None:
                continue
            try:
                out[i, j] = float(v)
            except (TypeError, ValueError):
                out[i, j] = np.nan
    return out


@dataclass
class Preprocessor:
    """Fit-on-train-only imputation + winsorization + standardization."""

    feature_names: list[str] = field(default_factory=list)
    winsor_lower_q: float = 0.01
    winsor_upper_q: float = 0.99
    _median: np.ndarray | None = None
    _lo: np.ndarray | None = None
    _hi: np.ndarray | None = None
    _mean: np.ndarray | None = None
    _std: np.ndarray | None = None
    fitted: bool = False

    def fit(self, X: np.ndarray) -> "Preprocessor":
        if X.ndim != 2:
            raise ValueError("X must be 2D")
        self._median = np.nanmedian(_safe(X), axis=0)
        # Winsor bounds computed on imputed train data.
        imputed = self._impute(X)
        self._lo = np.nanquantile(imputed, self.winsor_lower_q, axis=0)
        self._hi = np.nanquantile(imputed, self.winsor_upper_q, axis=0)
        clipped = np.clip(imputed, self._lo, self._hi)
        self._mean = clipped.mean(axis=0)
        std = clipped.std(axis=0)
        std[std == 0] = 1.0
        self._std = std
        self.fitted = True
        return self

    def transform(self, X: np.ndarray) -> np.ndarray:
        if not self.fitted:
            raise RuntimeError("Preprocessor not fitted")
        imputed = self._impute(X)
        clipped = np.clip(imputed, self._lo, self._hi)
        return (clipped - self._mean) / self._std

    def fit_transform(self, X: np.ndarray) -> np.ndarray:
        return self.fit(X).transform(X)

    def _impute(self, X: np.ndarray) -> np.ndarray:
        assert self._median is not None
        out = X.copy()
        inds = np.where(np.isnan(out))
        if inds[0].size:
            out[inds] = np.take(self._median, inds[1])
        # Any still-NaN (all-NaN column at fit) -> 0.
        out = np.nan_to_num(out, nan=0.0)
        return out


def _safe(X: np.ndarray) -> np.ndarray:
    # nanmedian warns on all-NaN columns; replace those columns' NaNs first.
    col_all_nan = np.all(np.isnan(X), axis=0)
    if col_all_nan.any():
        X = X.copy()
        X[:, col_all_nan] = 0.0
    return X
