"""Automated leakage detectors.

These run in tests and (optionally) as a training gate. They are deliberately
conservative: a positive finding blocks approval. Four families of check:

  1. **Look-ahead** - any feature whose availability postdates its prediction
     timestamp. (The schema blocks this per-feature; this is the dataset-level
     backstop.)
  2. **Outcome-window purge** - no training sample's outcome window may reach
     into the validation/calibration region.
  3. **Chronological ordering** - train precedes calibration precedes
     validation in time, with no interleaving.
  4. **Feature-target leakage** - a feature almost perfectly predictive of the
     target (|corr| ~ 1) is a red flag for a target proxy sneaking in.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Sequence

from catalystiq.ml.features.schema import PointInTimeFeature
from catalystiq.ml.validation.splitter import Fold, SampleWindow


@dataclass
class LeakageReport:
    ok: bool
    findings: list[str] = field(default_factory=list)

    def add(self, msg: str) -> None:
        self.ok = False
        self.findings.append(msg)


def check_dataset_lookahead(features: Sequence[PointInTimeFeature]) -> LeakageReport:
    report = LeakageReport(ok=True)
    for f in features:
        if f.available_at_timestamp > f.prediction_timestamp:
            report.add(
                f"look-ahead: {f.symbol}/{f.feature_name} available_at "
                f"{f.available_at_timestamp.isoformat()} > prediction "
                f"{f.prediction_timestamp.isoformat()}"
            )
    return report


def check_outcome_window_purge(
    fold: Fold, windows: dict[int, SampleWindow]
) -> LeakageReport:
    """Verify no training sample's outcome_end reaches the calib/val region."""
    report = LeakageReport(ok=True)
    if fold.calibration_span is None:
        return report
    boundary = fold.calibration_span[0]  # calib_start
    for idx in fold.train:
        w = windows.get(idx)
        if w is None:
            continue
        if w.outcome_end > boundary:
            report.add(
                f"purge violation: train sample {idx} outcome_end "
                f"{w.outcome_end.isoformat()} > boundary {boundary.isoformat()}"
            )
    return report


def assert_chronological_fold(fold: Fold, windows: dict[int, SampleWindow]) -> LeakageReport:
    """Train max time < calibration min time < validation min time."""
    report = LeakageReport(ok=True)

    def _times(idxs: list[int]) -> list[dt.datetime]:
        return [windows[i].prediction_time for i in idxs if i in windows]

    tr, ca, va = _times(fold.train), _times(fold.calibration), _times(fold.validation)
    if tr and ca and max(tr) >= min(ca):
        report.add("train overlaps calibration in time")
    if tr and va and max(tr) >= min(va):
        report.add("train overlaps validation in time")
    if ca and va and max(ca) >= min(va):
        report.add("calibration overlaps validation in time")
    return report


def check_feature_target_leakage(
    feature_values: Sequence[float],
    target_values: Sequence[float],
    *,
    feature_name: str = "feature",
    abs_corr_threshold: float = 0.999,
) -> LeakageReport:
    """Flag a feature that is near-perfectly correlated with the target.

    Pure-Python Pearson correlation (no numpy dependency) so it runs
    everywhere. Constant vectors can't leak (corr undefined -> treated as ok).
    """
    report = LeakageReport(ok=True)
    n = min(len(feature_values), len(target_values))
    if n < 3:
        return report
    xs = [float(v) for v in feature_values[:n]]
    ys = [float(v) for v in target_values[:n]]
    mx = sum(xs) / n
    my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx <= 0 or syy <= 0:
        return report
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    corr = sxy / (sxx**0.5 * syy**0.5)
    if abs(corr) >= abs_corr_threshold:
        report.add(
            f"feature-target leakage: '{feature_name}' has |corr|={abs(corr):.4f} "
            f">= {abs_corr_threshold} with the target"
        )
    return report
