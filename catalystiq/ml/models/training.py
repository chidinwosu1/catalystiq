"""Shared training orchestration used by every model family.

Turns a :class:`~catalystiq.ml.dataset.builder.TrainingDataset` into the
chronological train / calibration / evaluation matrices the head trainers
need, carving a final untouched holdout and running walk-forward folds for
stability reporting. Preprocessing is fit only on the training portion (the
head trainers enforce this via :class:`Preprocessor`).
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Callable, Sequence

import numpy as np

from catalystiq.ml.dataset.builder import TrainingDataset, TrainingExample
from catalystiq.ml.models.base import to_matrix
from catalystiq.ml.validation.splitter import (
    Fold,
    SampleWindow,
    make_final_holdout,
    make_walk_forward_folds,
)


def stable_feature_names(dataset: TrainingDataset) -> list[str]:
    names: set[str] = set()
    for ex in dataset.examples:
        names.update(ex.features.keys())
    return sorted(names)


def _windows(examples: Sequence[TrainingExample]) -> list[SampleWindow]:
    out: list[SampleWindow] = []
    for i, ex in enumerate(examples):
        outcome_end = ex.entry_session + dt.timedelta(days=int(ex.horizon_days) * 2 + 1)
        out.append(SampleWindow(index=i, prediction_time=ex.prediction_timestamp, outcome_end=outcome_end))
    return out


@dataclass
class ChronoSplit:
    train_idx: list[int]
    calib_idx: list[int]
    holdout_idx: list[int]
    folds: list[Fold]
    holdout_start: dt.datetime | None
    develop_purged: int = 0


def chronological_split(
    dataset: TrainingDataset,
    *,
    holdout_fraction: float = 0.2,
    calibration_fraction: float = 0.2,
    embargo_days: int = 5,
    n_folds: int = 4,
) -> ChronoSplit:
    windows = _windows(dataset.examples)
    if not windows:
        return ChronoSplit([], [], [], [], None)
    embargo = dt.timedelta(days=embargo_days)
    ho = make_final_holdout(windows, holdout_fraction=holdout_fraction, embargo=embargo)
    develop_windows = [w for w in windows if w.index in set(ho.develop)]

    # Split develop into train + calibration by time (calibration is the most
    # recent slice of develop, before the holdout).
    develop_sorted = sorted(develop_windows, key=lambda w: w.prediction_time)
    n = len(develop_sorted)
    cut = int(n * (1 - calibration_fraction))
    train_idx = [w.index for w in develop_sorted[:cut]]
    calib_idx = [w.index for w in develop_sorted[cut:]]

    folds = make_walk_forward_folds(
        develop_windows, n_folds=n_folds, embargo=embargo,
        calibration_fraction=calibration_fraction,
    )
    return ChronoSplit(
        train_idx=train_idx,
        calib_idx=calib_idx,
        holdout_idx=list(ho.holdout),
        folds=folds,
        holdout_start=ho.holdout_start,
        develop_purged=ho.purged_count,
    )


def matrices_for(
    dataset: TrainingDataset,
    feature_names: list[str],
    idx: Sequence[int],
    label_getter: Callable[[TrainingExample], float | int | None],
) -> tuple[np.ndarray, np.ndarray, list[int]]:
    """Return (X, y, kept_idx) for the examples at ``idx`` whose label is not
    None. Rows with a missing label are dropped (never imputed)."""
    rows: list[dict] = []
    ys: list[float] = []
    kept: list[int] = []
    for i in idx:
        ex = dataset.examples[i]
        label = label_getter(ex)
        if label is None:
            continue
        rows.append(ex.features)
        ys.append(float(label))
        kept.append(i)
    X = to_matrix(rows, feature_names)
    y = np.asarray(ys, dtype=float)
    return X, y, kept


@dataclass
class SplitReport:
    n_examples: int
    n_train: int
    n_calibration: int
    n_holdout: int
    n_folds: int
    holdout_start: str | None
    develop_purged: int
    fold_purged: list[int] = field(default_factory=list)
    fold_embargoed: list[int] = field(default_factory=list)

    @classmethod
    def from_split(cls, dataset: TrainingDataset, split: ChronoSplit) -> "SplitReport":
        return cls(
            n_examples=dataset.size,
            n_train=len(split.train_idx),
            n_calibration=len(split.calib_idx),
            n_holdout=len(split.holdout_idx),
            n_folds=len(split.folds),
            holdout_start=split.holdout_start.isoformat() if split.holdout_start else None,
            develop_purged=split.develop_purged,
            fold_purged=[f.purged_count for f in split.folds],
            fold_embargoed=[f.embargoed_count for f in split.folds],
        )
