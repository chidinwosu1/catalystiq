"""Deterministic tests for the historical validation + MLflow experiment runner.

All tests use a seeded, clearly-SYNTHETIC dataset and the in-memory
RecordingTracker, so they run without a live MLflow and without network. They
verify: parent/child run structure, that the required params/metrics/artifacts
are logged, reproducibility, out-of-fold enforcement for Model 4, final-holdout
isolation, fail-closed + gate-refusal behavior, and artifact creation on disk.

Synthetic data is unit-test only and can never be approved (asserted).
"""
import datetime as dt

import numpy as np
import pytest

from catalystiq.config import Settings
from catalystiq.ml.dataset.builder import TrainingDataset, TrainingExample
from catalystiq.ml.flags import MLDisabledError
from catalystiq.ml.labels.outcomes import Direction, OutcomeLabels
from catalystiq.ml.models.base import sklearn_available

pytestmark = pytest.mark.skipif(not sklearn_available(), reason="scikit-learn not installed")

_SECTORS = {"AAA": "Tech", "BBB": "Energy", "CCC": "Tech", "DDD": "Finance"}


def build_synthetic_dataset(n: int = 300, seed: int = 3) -> TrainingDataset:
    """A seeded, labeled, clearly-synthetic dataset with the fields the gate and
    the models need (feature_completeness, rule-based score, market/sector)."""
    rng = np.random.default_rng(seed)
    base = dt.datetime(2019, 1, 1)
    ds = TrainingDataset(is_synthetic=True)
    syms = list(_SECTORS)
    for i in range(n):
        ts = base + dt.timedelta(days=i)
        sym = syms[i % len(syms)]
        rsi = float(rng.uniform(20, 80))
        mom = float(rng.normal(0, 1))
        signal = 0.6 * (rsi - 50) / 30 + 0.5 * mom
        net = float(0.01 * signal + rng.normal(0, 0.03))
        feats = {
            "rsi_14": rsi, "momentum_20d": mom, "atr_14": float(rng.uniform(1, 3)),
            "relative_volume_20d": float(rng.uniform(0.5, 2.5)),
            "rule_based_setup_strength": float(max(0, min(100, 50 + signal * 20))),
            "estimated_spread_bps": float(rng.uniform(1, 20)),
            "market_return_20d": float(rng.normal(0, 0.02)),
            "sector_return_20d": float(rng.normal(0, 0.02)),
            "market_regime": float(i % 3),
            "overnight_gap_pct": float(rng.normal(0, 1.5)),
            "feature_completeness": float(rng.uniform(0.75, 0.95)),
        }
        lab = OutcomeLabels(
            symbol=sym, direction=Direction.LONG, horizon_days=5,
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
        ds.examples.append(TrainingExample(sym, ts, ts + dt.timedelta(days=1), "long", 5, feats, lab))
    return ds


def _enabling() -> Settings:
    return Settings(action_api_key="k", enable_ml=True, enable_ml_training=True)


def _run(tracker=None, dataset=None, **kw):
    from catalystiq.ml.experiment import run_experiment

    return run_experiment(
        dataset_by_horizon={5: dataset or build_synthetic_dataset()},
        horizons=[5], settings=_enabling(), tracker=tracker,
        sector_resolver=lambda s: _SECTORS.get(s), is_synthetic_data=True, **kw,
    )


# --- fail-closed -----------------------------------------------------------
def test_fails_closed_when_training_disabled():
    from catalystiq.ml.experiment import run_experiment
    from catalystiq.ml.tracking import RecordingTracker

    with pytest.raises(MLDisabledError):
        run_experiment(
            dataset_by_horizon={5: build_synthetic_dataset(n=40)},
            horizons=[5], settings=Settings(action_api_key="k"),  # disabled
            tracker=RecordingTracker(),
        )


# --- parent/child structure ------------------------------------------------
def test_parent_child_run_structure():
    from catalystiq.ml.tracking import RecordingTracker

    tr = RecordingTracker()
    _run(tracker=tr)

    assert len(tr.parent_runs) == 1
    parent = tr.parent_runs[0]
    assert parent.name.startswith("experiment:")

    horizon = tr.runs_by_name("horizon:5d")
    assert len(horizon) == 1
    assert horizon[0].parent_id == parent.run_id

    hz_children = {c.name for c in tr.children_of(horizon[0])}
    for expected in ("model_1:5d", "model_2:5d", "model_3:5d",
                     "oof:models_1_3", "model_4:5d", "model_5:5d"):
        assert expected in hz_children, f"missing child run {expected}"

    # Model 1 has per-fold child runs and per-candidate child runs.
    m1 = tr.runs_by_name("model_1:5d")[0]
    m1_children = {c.name for c in tr.children_of(m1)}
    assert any(name.startswith("model_1:5d:fold_") for name in m1_children)
    assert "model_1:net_profit:candidate" in m1_children


# --- required logging ------------------------------------------------------
def test_logs_required_params_metrics_and_tags():
    from catalystiq.ml.tracking import RecordingTracker

    tr = RecordingTracker()
    _run(tracker=tr)
    parent = tr.parent_runs[0]
    # experiment-level: contract versions, commit, symbols, horizons
    for key in ("feature_contract_version", "label_contract_version", "seed",
                "split_protocol_version", "horizons"):
        assert key in parent.params

    horizon = tr.runs_by_name("horizon:5d")[0]
    for key in ("dataset_version", "dataset_hash", "embargo_days",
                "holdout_fraction", "n_walk_forward_folds", "holdout_boundary_holdout_start"):
        assert key in horizon.params
    # coverage + class balance metrics logged
    assert any(k.startswith("coverage") for k in horizon.metrics)
    assert "net_profit_positive_rate" in horizon.metrics
    assert horizon.tags.get("gate_passed") == "True"

    # Model 1 holdout classification battery logged.
    m1 = tr.runs_by_name("model_1:5d")[0]
    for metric in ("holdout.roc_auc", "holdout.pr_auc", "holdout.brier_score",
                   "holdout.log_loss", "holdout.expected_calibration_error"):
        assert metric in m1.metrics
    # deterministic scorer baseline logged for comparison
    assert any(k.startswith("scorer_baseline") for k in m1.metrics)
    # per-model sliced holdout metrics (sector/regime/confidence) logged
    assert "model_1/sliced_metrics.json" in m1.artifacts
    assert any(k.startswith("sector.") for k in m1.metrics)

    # survivorship-bias caveat is prominent at the parent run
    assert parent.tags.get("survivorship_bias") == "True"
    assert "survivorship_bias_warning" in parent.params
    assert "data_source_caveats.json" in parent.artifacts
    # regime coverage logged at the horizon
    assert "n_market_regimes" in horizon.metrics
    assert "regime_coverage.json" in horizon.artifacts


def test_survivorship_and_regime_reporting():
    report = _run()
    assert report.data_source_caveats["survivorship_bias"] is True
    assert "delisted" in report.data_source_caveats["survivorship_bias_warning"].lower()
    hz = report.horizons_results[0]
    # the seeded fixture spans 3 synthetic regimes
    assert len([k for k in hz.regime_coverage if k != "unknown"]) == 3


def test_single_regime_dataset_warns():
    from catalystiq.ml.experiment import _regime_coverage

    ds = build_synthetic_dataset()
    for ex in ds.examples:
        ex.features["market_regime"] = 1.0  # collapse to one regime
    cov = _regime_coverage(ds)
    assert list(cov) == ["regime_1"]


def test_ranking_uses_out_of_fold_inputs_only():
    report = _run()
    hz = report.horizons_results[0]
    assert hz.ranking["all_inputs_out_of_fold"] is True
    assert hz.ranking["n_examples"] > 0
    assert hz.ranking["chosen"] in ("baseline_composite", "ml_cross_sectional")
    # trading metrics are computed after costs
    assert "universe_mean_net_return_after_costs" in hz.ranking["trading_metrics_after_costs"]


def test_final_holdout_isolation():
    from catalystiq.ml.experiment import run_experiment
    from catalystiq.ml.models.training import chronological_split
    from catalystiq.ml.tracking import RecordingTracker

    ds = build_synthetic_dataset()
    split = chronological_split(ds)
    holdout = set(split.holdout_idx)
    develop = set(split.train_idx) | set(split.calib_idx)
    for f in split.folds:
        develop |= set(f.train) | set(f.calibration) | set(f.validation)
    # holdout is disjoint from every development index set
    assert holdout and not (holdout & develop)

    report = run_experiment(
        dataset_by_horizon={5: ds}, horizons=[5], settings=_enabling(),
        tracker=RecordingTracker(), sector_resolver=lambda s: _SECTORS.get(s),
        is_synthetic_data=True,
    )
    hz = report.horizons_results[0]
    assert hz.oof["holdout_never_predicted"] is True
    assert hz.oof["n_holdout"] == len(holdout)


def test_reproducible_dataset_and_metrics():
    r1 = _run()
    r2 = _run()
    h1, h2 = r1.horizons_results[0], r2.horizons_results[0]
    assert h1.dataset_hash == h2.dataset_hash
    assert h1.dataset_version == h2.dataset_version
    # same seed/data -> identical holdout ROC-AUC for Model 1
    a1 = h1.models[0].holdout_metrics.get("roc_auc")
    a2 = h2.models[0].holdout_metrics.get("roc_auc")
    assert a1 == pytest.approx(a2, rel=1e-9, nan_ok=True)


def test_gate_refuses_training_on_insufficient_data():
    from catalystiq.ml.tracking import RecordingTracker

    tr = RecordingTracker()
    report = _run(tracker=tr, dataset=build_synthetic_dataset(n=40))  # too small
    hz = report.horizons_results[0]
    assert hz.gate_passed is False
    assert hz.models == []  # nothing trained
    horizon = tr.runs_by_name("horizon:5d")[0]
    assert horizon.tags.get("training_refused") == "True"
    # the gate verdict artifact is still recorded for the audit trail
    assert "gate_verdict.json" in horizon.artifacts


def test_synthetic_run_is_labeled_synthetic_and_not_approved(test_db_session):
    from catalystiq.ml import registry
    from catalystiq.ml.experiment import run_experiment
    from catalystiq.ml.tracking import RecordingTracker

    report = run_experiment(
        test_db_session, dataset_by_horizon={5: build_synthetic_dataset()},
        horizons=[5], settings=_enabling(), tracker=RecordingTracker(),
        sector_resolver=lambda s: _SECTORS.get(s), is_synthetic_data=True,
        register=True,
    )
    assert report.horizons_results[0].is_synthetic is True
    arts = registry.list_artifacts(test_db_session)
    assert arts, "candidate artifacts should be registered"
    assert all(a.approval_status == "candidate" and a.is_synthetic for a in arts)
    assert registry.get_approved(
        test_db_session, model_family="model_1", horizon_days=5, trade_direction="long") is None


def test_artifacts_written_to_disk(tmp_path):
    from catalystiq.ml.experiment import run_experiment
    from catalystiq.ml.tracking import RecordingTracker

    tr = RecordingTracker(output_dir=str(tmp_path))
    run_experiment(
        dataset_by_horizon={5: build_synthetic_dataset()}, horizons=[5],
        settings=_enabling(), tracker=tr, sector_resolver=lambda s: _SECTORS.get(s),
        is_synthetic_data=True,
    )
    produced = {str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*") if p.is_file()}
    # a representative sample across the required artifact list
    for expected in (
        "feature_manifest.json", "dataset_manifest.json", "provenance_summary.json",
        "gate_verdict.json", "fold_definitions.json", "model_comparison_report.json",
        "final_holdout_report.json", "model_1/failure_cases.json",
        "model_1/net_profit/reliability_bins.json", "model_4/ranking_report.json",
        "model_5/aggregate_response_tables.json",
    ):
        assert expected in produced, f"missing artifact {expected}"
    # at least one calibration/ROC plot rendered (matplotlib present in CI)
    assert any(p.endswith("calibration.png") for p in produced)
