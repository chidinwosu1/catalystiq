"""Model 1-3 training pipelines on SYNTHETIC data (unit-test use only).

Synthetic data is permitted for unit tests only; these artifacts are never
approved for user-facing use. Skips cleanly if scikit-learn is unavailable.
"""
import datetime as dt

import numpy as np
import pytest

from catalystiq.ml.dataset.builder import TrainingDataset, TrainingExample
from catalystiq.ml.labels.outcomes import Direction, OutcomeLabels
from catalystiq.ml.models.base import sklearn_available

pytestmark = pytest.mark.skipif(not sklearn_available(), reason="scikit-learn not installed")


def _synthetic_dataset(n=1000, seed=1) -> TrainingDataset:
    rng = np.random.default_rng(seed)
    base = dt.datetime(2019, 1, 1)
    ds = TrainingDataset(is_synthetic=True)
    for i in range(n):
        ts = base + dt.timedelta(days=i)
        rsi = float(rng.uniform(20, 80))
        mom = float(rng.normal(0, 1))
        atr = float(rng.uniform(1, 3))
        signal = 0.6 * (rsi - 50) / 30 + 0.5 * mom
        net = float(0.01 * signal + rng.normal(0, 0.03))
        feats = {"rsi_14": rsi, "momentum_20d": mom, "atr_14": atr,
                 "relative_volume_20d": float(rng.uniform(0.5, 2))}
        lab = OutcomeLabels(
            symbol="SYN", direction=Direction.LONG, horizon_days=5,
            executable_entry_price=100, target_price=105, stop_price=95,
            net_profit_label=int(net > 0),
            target_before_stop_label=int(signal + rng.normal(0, 0.5) > 0),
            net_terminal_return=net,
            max_adverse_excursion=float(-abs(rng.normal(0.01, 0.01))),
            max_favorable_excursion=float(abs(rng.normal(0.02, 0.01))),
            stop_breach_label=int(rng.uniform(0, 1) < 0.3),
            gap_beyond_stop_label=int(rng.uniform(0, 1) < 0.04),
            gross_terminal_return=net, round_trip_cost=0.001,
            both_touched=False, excluded_reason=None,
        )
        ds.examples.append(TrainingExample("SYN", ts, ts + dt.timedelta(days=1), "long", 5, feats, lab))
    return ds


def test_model_one_trains_and_predicts_calibrated():
    from catalystiq.ml.models.model_one import train_model_one

    ds = _synthetic_dataset()
    rep = train_model_one(ds, horizon_days=5)
    assert rep.artifact is not None
    pred = rep.artifact.predict({"rsi_14": 70, "momentum_20d": 1.2, "atr_14": 2, "relative_volume_20d": 1.4})
    assert 0.0 <= pred.net_profit_probability <= 1.0
    assert 0.0 <= pred.target_before_stop_probability <= 1.0
    assert pred.calibration_status in {"acceptable", "needs_review", "poor", "unknown"}
    # walk-forward folds were produced and holdout carved.
    assert rep.split.n_folds >= 1
    assert rep.split.n_holdout > 0


def test_model_two_quantiles_are_monotone():
    from catalystiq.ml.models.model_two import train_model_two

    ds = _synthetic_dataset()
    rep = train_model_two(ds, horizon_days=5)
    assert rep.artifact is not None
    pred = rep.artifact.predict({"rsi_14": 70, "momentum_20d": 1.2, "atr_14": 2, "relative_volume_20d": 1.4})
    vals = [pred.net_return_quantiles[k] for k in ("q10", "q25", "q50", "q75", "q90")]
    assert vals == sorted(vals)
    assert pred.quantile_crossing_detected is False


def test_model_three_gap_insufficient_evidence_when_rare():
    from catalystiq.ml.models.model_three import train_model_three

    ds = _synthetic_dataset()
    rep = train_model_three(ds, horizon_days=5)
    assert rep.artifact is not None
    pred = rep.artifact.predict({"rsi_14": 70, "momentum_20d": 1.2, "atr_14": 2, "relative_volume_20d": 1.4})
    # gap events are ~4% -> too rare in the holdout -> insufficient_evidence
    assert pred.gap_beyond_stop_probability == "insufficient_evidence"
    assert pred.median_adverse_excursion is not None
    assert pred.severe_adverse_excursion <= pred.median_adverse_excursion
