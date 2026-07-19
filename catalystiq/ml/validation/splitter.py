"""Purged, embargoed chronological walk-forward splitter.

Random train/test splits are forbidden for this system: outcome windows
overlap in time, so a random split leaks the future into the past. This
module produces strictly chronological folds and, critically:

  * **Purges** every training sample whose *outcome window* extends into (or
    past the start of) the calibration/validation region. A 20-day label
    computed on day T uses prices through T+20; if T+20 lands inside the
    validation window, that training sample has seen validation-era data.

  * **Embargoes** an additional gap around each boundary, dropping training
    samples whose outcome ends within ``embargo`` of the boundary, to absorb
    serial correlation that purging alone doesn't remove.

  * Reserves **one final, untouched chronological holdout** at the end of the
    timeline (via :func:`make_final_holdout`) that no fold, tuning, or
    calibration step is ever allowed to see.

Each fold exposes four disjoint, time-ordered index sets - train, calibration,
validation - carved from the development span, in that temporal order. The
model base fits ALL preprocessing (imputation, scaling, winsorization, feature
selection, tuning) on ``train`` only; calibration/validation/holdout never
influence preprocessing.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field


@dataclass(frozen=True)
class SampleWindow:
    """One example's temporal footprint.

    ``prediction_time`` is when the features are known and the prediction is
    made. ``outcome_end`` is the last moment whose data determines the label
    (entry session + horizon). Purging compares ``outcome_end`` to region
    boundaries; ordering uses ``prediction_time``.
    """

    index: int
    prediction_time: dt.datetime
    outcome_end: dt.datetime


@dataclass(frozen=True)
class Fold:
    fold_id: int
    train: list[int]
    calibration: list[int]
    validation: list[int]
    train_end: dt.datetime | None
    calibration_span: tuple[dt.datetime, dt.datetime] | None
    validation_span: tuple[dt.datetime, dt.datetime] | None
    purged_count: int = 0
    embargoed_count: int = 0


@dataclass(frozen=True)
class HoldoutSplit:
    develop: list[int]           # everything usable for training/tuning/calib
    holdout: list[int]           # untouched final chronological holdout
    holdout_start: dt.datetime | None
    purged_count: int = 0


def _sorted(samples: list[SampleWindow]) -> list[SampleWindow]:
    return sorted(samples, key=lambda s: (s.prediction_time, s.index))


def _time_at_fraction(times: list[dt.datetime], frac: float) -> dt.datetime:
    if not times:
        raise ValueError("no samples")
    start, end = times[0], times[-1]
    span = (end - start).total_seconds()
    return start + dt.timedelta(seconds=span * max(0.0, min(1.0, frac)))


def make_final_holdout(
    samples: list[SampleWindow],
    *,
    holdout_fraction: float = 0.2,
    embargo: dt.timedelta = dt.timedelta(days=5),
) -> HoldoutSplit:
    """Carve the final untouched holdout by time.

    Samples at/after ``holdout_start`` form the holdout. Any development
    sample whose outcome window reaches into ``holdout_start - embargo`` is
    PURGED from ``develop`` so the holdout stays pristine.
    """
    if not samples:
        return HoldoutSplit(develop=[], holdout=[], holdout_start=None)
    ordered = _sorted(samples)
    times = [s.prediction_time for s in ordered]
    holdout_start = _time_at_fraction(times, 1.0 - holdout_fraction)
    boundary = holdout_start - embargo

    develop: list[int] = []
    holdout: list[int] = []
    purged = 0
    for s in ordered:
        if s.prediction_time >= holdout_start:
            holdout.append(s.index)
        elif s.outcome_end <= boundary:
            develop.append(s.index)
        else:
            purged += 1  # overlaps the holdout region -> dropped
    return HoldoutSplit(
        develop=develop, holdout=holdout, holdout_start=holdout_start, purged_count=purged
    )


def make_walk_forward_folds(
    samples: list[SampleWindow],
    *,
    n_folds: int = 4,
    calibration_fraction: float = 0.25,
    embargo: dt.timedelta = dt.timedelta(days=5),
    expanding: bool = True,
) -> list[Fold]:
    """Build ``n_folds`` chronological walk-forward folds over the samples.

    The development span (already excluding any final holdout, if the caller
    carved one first) is divided into ``n_folds`` equal-time validation
    blocks. For each block:

      * ``validation`` = samples whose prediction_time falls in the block.
      * ``calibration`` = samples in the ``calibration_fraction`` slice of
        time immediately preceding the validation block.
      * ``train`` = samples before the calibration slice whose outcome window
        ends at or before ``calib_start - embargo`` (purge + embargo). With
        ``expanding=True`` train grows each fold; otherwise it is a rolling
        window of one block width.
    """
    if not samples:
        return []
    if n_folds < 1:
        raise ValueError("n_folds must be >= 1")

    ordered = _sorted(samples)
    times = [s.prediction_time for s in ordered]
    start, end = times[0], times[-1]
    total = (end - start).total_seconds()
    if total <= 0:
        # All samples share a timestamp - no valid chronological split.
        return []

    block = total / n_folds
    folds: list[Fold] = []

    for i in range(n_folds):
        val_start = start + dt.timedelta(seconds=block * i)
        val_end = start + dt.timedelta(seconds=block * (i + 1))
        # Include the final endpoint in the last block.
        is_last = i == n_folds - 1
        calib_width = block * calibration_fraction
        calib_start = val_start - dt.timedelta(seconds=calib_width)
        # First fold has no room before it for a train set - skip it.
        if calib_start <= start:
            continue

        boundary = calib_start - embargo
        rolling_start = calib_start - dt.timedelta(seconds=block) if not expanding else start

        train: list[int] = []
        calibration: list[int] = []
        validation: list[int] = []
        purged = 0
        embargoed = 0

        for s in ordered:
            pt = s.prediction_time
            if val_start <= pt < val_end or (is_last and pt == val_end):
                validation.append(s.index)
            elif calib_start <= pt < val_start:
                calibration.append(s.index)
            elif pt < calib_start and pt >= rolling_start:
                if s.outcome_end <= boundary:
                    train.append(s.index)
                elif s.outcome_end < calib_start:
                    embargoed += 1  # within embargo of the boundary
                else:
                    purged += 1  # outcome overlaps calib/validation region
            # else: outside this fold's rolling window

        if not train or not validation:
            continue

        folds.append(
            Fold(
                fold_id=i,
                train=train,
                calibration=calibration,
                validation=validation,
                train_end=boundary,
                calibration_span=(calib_start, val_start),
                validation_span=(val_start, val_end),
                purged_count=purged,
                embargoed_count=embargoed,
            )
        )

    return folds
