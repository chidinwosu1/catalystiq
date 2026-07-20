"""Chronological training dry-run harness.

Two verification paths keep runtime bounded:
  * the PROVIDER path (seeded Silver -> point-in-time features) on a small set,
    proving the provider/builder/split/leakage/coverage chain end-to-end;
  * the DATASET path (a fast pre-built synthetic dataset) proving the
    model-fitting orchestration + candidate registration without paying the
    per-example snapshot cost.
Synthetic data is unit-test only and can never be approved.
"""
import datetime as dt
import math

import numpy as np
import pytest

from catalystiq.config import Settings
from catalystiq.db import models
from catalystiq.ml.dataset.builder import TrainingDataset, TrainingExample
from catalystiq.ml.dry_run import run_training_dry_run
from catalystiq.ml.flags import MLDisabledError
from catalystiq.ml.labels.outcomes import Direction, OutcomeLabels
from catalystiq.ml.models.base import sklearn_available


def _enabling():
    return Settings(action_api_key="k", enable_ml=True, enable_ml_training=True)


# --- provider path ----------------------------------------------------------
def _seed(db, sym, *, n=280, seed=1.0, drift=0.0006):
    t = models.Ticker(symbol=sym.upper(), sector="Technology")
    db.add(t)
    db.flush()
    base = dt.date(2019, 1, 1)
    p = 100.0 * seed
    now = dt.datetime(2019, 1, 1)
    for i in range(n):
        p *= 1 + drift + 0.01 * math.sin(i / 7)
        db.add(models.SilverPriceBar(
            ticker_id=t.id, date=base + dt.timedelta(days=i),
            open=p * 0.995, high=p * 1.015, low=p * 0.985, close=p,
            volume=1_000_000 + i, data_quality_status="ok", created_at=now, updated_at=now))
    db.flush()


def test_fails_closed_when_training_disabled(test_db_session):
    _seed(test_db_session, "AAA")
    with pytest.raises(MLDisabledError):
        run_training_dry_run(
            test_db_session, symbols=["AAA"],
            prediction_timestamps=[dt.datetime(2019, 8, 5, 20)],
            settings=Settings(action_api_key="k"),  # disabled
        )


def test_provider_path_diagnostics(test_db_session):
    for s, sd, dr in [("AAA", 1.0, 0.0007), ("BBB", 2.0, -0.0003), ("SPY", 4.0, 0.0004)]:
        _seed(test_db_session, s, seed=sd, drift=dr)
    dates = [dt.datetime(2019, 8, 5, 20) + dt.timedelta(days=7 * i) for i in range(9)]
    report = run_training_dry_run(
        test_db_session, symbols=["AAA", "BBB"], prediction_timestamps=dates,
        horizon_days=5, is_synthetic_data=True, settings=_enabling(),
        min_examples_to_fit=10_000,  # skip the (slow) fit here; covered below
    )
    assert report.dataset_size > 0
    assert report.is_synthetic and report.training_data_version.startswith("synthetic-")
    # chronological, leakage-free folds
    assert report.folds.chronology_ok is True
    assert report.folds.leakage_findings == []
    # price-derived features present; known gaps flagged
    assert report.coverage.per_group_present_rate["adjusted_ohlcv"] > 0.9
    assert "earnings_proximity" in report.coverage.always_missing_groups
    assert "macro_bls_bea" in report.coverage.always_missing_groups
    assert report.labels.net_return_labeled > 0
    assert isinstance(report.to_dict(), dict)
    # fitting skipped by the high threshold, reported honestly
    assert report.models.trained is False


# --- dataset path (fast) ----------------------------------------------------
def _synthetic_dataset(n=260, seed=3):
    rng = np.random.default_rng(seed)
    base = dt.datetime(2019, 1, 1)
    ds = TrainingDataset(is_synthetic=True)
    for i in range(n):
        ts = base + dt.timedelta(days=i)
        rsi = float(rng.uniform(20, 80)); mom = float(rng.normal(0, 1))
        signal = 0.6 * (rsi - 50) / 30 + 0.5 * mom
        net = float(0.01 * signal + rng.normal(0, 0.03))
        feats = {"rsi_14": rsi, "momentum_20d": mom, "atr_14": float(rng.uniform(1, 3)),
                 "relative_volume_20d": float(rng.uniform(0.5, 2))}
        lab = OutcomeLabels(
            symbol="SYN", direction=Direction.LONG, horizon_days=5,
            executable_entry_price=100, target_price=105, stop_price=95,
            net_profit_label=int(net > 0), target_before_stop_label=int(signal + rng.normal(0, 0.5) > 0),
            net_terminal_return=net, max_adverse_excursion=float(-abs(rng.normal(0.01, 0.01))),
            max_favorable_excursion=float(abs(rng.normal(0.02, 0.01))),
            stop_breach_label=int(rng.uniform(0, 1) < 0.3), gap_beyond_stop_label=int(rng.uniform(0, 1) < 0.04),
            gross_terminal_return=net, round_trip_cost=0.001, both_touched=False, excluded_reason=None)
        ds.examples.append(TrainingExample("SYN", ts, ts + dt.timedelta(days=1), "long", 5, feats, lab))
    return ds


@pytest.mark.skipif(not sklearn_available(), reason="scikit-learn not installed")
def test_dataset_path_fits_and_registers_candidates_only(test_db_session):
    ds = _synthetic_dataset()
    report = run_training_dry_run(
        db=test_db_session, dataset=ds, horizon_days=5, settings=_enabling(),
        register=True, min_examples_to_fit=60,
    )
    assert report.models.trained is True
    assert report.folds.chronology_ok is True
    # sufficiency verdict is present and structured
    assert "sufficient_for_training" in report.sufficiency

    from catalystiq.ml import registry
    arts = registry.list_artifacts(test_db_session)
    assert arts, "candidate artifacts should be registered"
    assert all(a.approval_status == "candidate" and a.is_synthetic for a in arts)
    # nothing is approved / servable
    assert registry.get_approved(
        test_db_session, model_family="model_1", horizon_days=5, trade_direction="long") is None


def test_dataset_path_also_fails_closed(test_db_session):
    with pytest.raises(MLDisabledError):
        run_training_dry_run(dataset=_synthetic_dataset(n=10), settings=Settings(action_api_key="k"))
