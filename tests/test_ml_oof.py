"""Deterministic tests for out-of-fold Model 1-3 prediction generation.

OOF predictions are what makes a leakage-free Model 4 ranker possible, so these
tests assert the two properties the ranker depends on: the untouched final
holdout is NEVER predicted, and predictions come only from the walk-forward
validation folds.
"""
import pytest

from catalystiq.ml.models.base import sklearn_available
from catalystiq.ml.models.training import chronological_split
from catalystiq.ml.oof import generate_oof_predictions
from tests.test_ml_experiment import build_synthetic_dataset

pytestmark = pytest.mark.skipif(not sklearn_available(), reason="scikit-learn not installed")


def test_oof_never_predicts_holdout():
    ds = build_synthetic_dataset()
    split = chronological_split(ds)
    holdout = set(split.holdout_idx)
    result = generate_oof_predictions(ds, horizon_days=5, direction="long")

    assert holdout, "expected a non-empty holdout"
    assert not (set(result.predictions) & holdout), "OOF must never predict holdout examples"
    assert set(result.predictions).issubset(set(result.validation_indices))
    assert set(result.holdout_indices) == holdout


def test_oof_produces_model_1_3_outputs_and_realized_labels():
    ds = build_synthetic_dataset()
    result = generate_oof_predictions(ds, horizon_days=5, direction="long",
                                      sector_of=lambda s: "Tech")
    assert result.n_folds >= 1
    assert result.coverage_rate > 0
    # at least some predictions carry the M1 net-profit probability and the
    # realized outcome needed for the ranker target.
    with_prob = [p for p in result.predictions.values() if p.net_profit_prob is not None]
    assert with_prob
    p = with_prob[0]
    assert 0.0 <= p.net_profit_prob <= 1.0
    assert p.net_terminal_return is not None
    assert p.sector == "Tech"


def test_oof_predictions_are_reproducible():
    ds = build_synthetic_dataset()
    r1 = generate_oof_predictions(ds, horizon_days=5, direction="long")
    r2 = generate_oof_predictions(ds, horizon_days=5, direction="long")
    assert set(r1.predictions) == set(r2.predictions)
    common = next(iter(set(r1.predictions) & {i for i, p in r2.predictions.items()
                                              if p.net_profit_prob is not None}), None)
    if common is not None and r1.predictions[common].net_profit_prob is not None:
        assert r1.predictions[common].net_profit_prob == pytest.approx(
            r2.predictions[common].net_profit_prob, rel=1e-9)
