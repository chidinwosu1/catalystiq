"""Historical model-validation + MLflow experiment orchestrator.

This is the real training/evaluation phase built ON TOP OF the existing
point-in-time dataset builder, dry-run sufficiency harness, purged/embargoed
walk-forward splitter and Model 1-5 implementations. It does NOT introduce a
second feature pipeline or re-implement any model - it wires the existing
pieces together and records the whole experiment to MLflow.

For each requested horizon it:

  1. builds the point-in-time training dataset (via the existing provider +
     ``TrainingExampleBuilder``) or accepts a pre-built one;
  2. runs the existing sufficiency / chronology / leakage / provenance /
     point-in-time checks and REFUSES to train that horizon if any fail;
  3. trains Models 1-3 with the existing purged, embargoed walk-forward
     validation (each head selects candidate-vs-baseline on the untouched final
     holdout);
  4. generates OUT-OF-FOLD Model 1-3 predictions and only then trains Model 4
     (the ranker refuses in-sample inputs), and builds Model 5's aggregate
     response tables as a separate evidence source;
  5. evaluates candidates against their simple baselines AND the deterministic
     Catalyst IQ rule-based scorer, then evaluates once on the untouched final
     holdout after selection, slicing by sector / regime / confidence band /
     holding period and reporting metrics after transaction costs;
  6. logs parameters, metrics and artifacts to a parent MLflow run with nested
     child runs per model, horizon, fold and candidate algorithm.

It is fail-closed: it refuses to run unless ``ENABLE_ML`` + ``ENABLE_ML_TRAINING``
are set in the passed (offline) settings. That authorization applies to this
process only - it never enables inference, serving, model approval, registry
promotion or order submission. Candidate artifacts, if registered, are always
``candidate`` (never approved), and synthetic datasets are flagged so they can
never be promoted.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import subprocess
from dataclasses import asdict, dataclass, field

import numpy as np

from catalystiq.config import Settings
from catalystiq.ml import (
    FEATURE_SCHEMA_VERSION,
    SPLIT_PROTOCOL_VERSION,
    TARGET_DEFINITION_VERSION,
)
from catalystiq.ml import flags
from catalystiq.ml.calibration import calibration_status, expected_calibration_error, reliability_bins
from catalystiq.ml.dataset.builder import (
    ExampleRequest,
    TrainingDataset,
    TrainingExampleBuilder,
)
from catalystiq.ml.dry_run import (
    _assess_sufficiency,
    _feature_coverage,
    _fold_diagnostics,
    _label_diagnostics,
)
from catalystiq.ml.evaluation.classification import classification_metrics
from catalystiq.ml.evaluation.quantile import quantile_metrics
from catalystiq.ml.evaluation.ranking import RankedItem, ranking_metrics
from catalystiq.ml.features.pit_provider import SilverPointInTimeProvider
from catalystiq.ml.features.schema import FEATURE_CATALOG, missing_indicator_name
from catalystiq.ml.models.base import to_matrix
from catalystiq.ml.models.model_four import (
    DEFAULT_RANKER_WEIGHTS,
    OpportunityInputs,
    RankerExample,
    baseline_opportunity_utility,
    realized_utility,
    train_model_four,
)
from catalystiq.ml.models.model_one import train_model_one
from catalystiq.ml.models.model_three import train_model_three
from catalystiq.ml.models.model_two import train_model_two
from catalystiq.ml.models.training import chronological_split
from catalystiq.ml.oof import generate_oof_predictions
from catalystiq.ml import plots
from catalystiq.ml.tracking import BaseTracker, get_tracker

# Acceptance thresholds for the experiment-level verdict. These are validation
# gates for a *report*, NOT model-approval criteria (approval is a separate,
# deliberate registry action that this runner never performs).
MIN_HOLDOUT_AUC = 0.55
MAX_HOLDOUT_ECE = 0.10
DEFAULT_SEED = 7


@dataclass
class ModelResult:
    family: str
    horizon_days: int
    trained: bool
    chosen: dict = field(default_factory=dict)
    holdout_metrics: dict = field(default_factory=dict)
    scorer_baseline_metrics: dict = field(default_factory=dict)
    sliced_metrics: dict = field(default_factory=dict)
    acceptance: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


@dataclass
class HorizonResult:
    horizon_days: int
    dataset_size: int
    is_synthetic: bool
    dataset_version: str
    dataset_hash: str
    gate_passed: bool
    gate: dict = field(default_factory=dict)
    split: dict = field(default_factory=dict)
    models: list[ModelResult] = field(default_factory=list)
    ranking: dict = field(default_factory=dict)
    behavior_model: dict = field(default_factory=dict)
    oof: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


@dataclass
class ExperimentReport:
    experiment_name: str
    tracking_backend: str
    symbols: list[str]
    benchmark: str
    direction: str
    horizons: list[int]
    date_range: tuple[str | None, str | None]
    code_commit: str | None
    feature_schema_version: str
    target_definition_version: str
    split_protocol_version: str
    horizons_results: list[HorizonResult] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def run_experiment(
    db=None,
    *,
    symbols: list[str] | None = None,
    benchmark: str = "SPY",
    start: dt.date | None = None,
    end: dt.date | None = None,
    step_days: int = 7,
    horizons: list[int] | None = None,
    direction: str = "long",
    dataset_by_horizon: dict[int, TrainingDataset] | None = None,
    is_synthetic_data: bool = False,
    settings: Settings | None = None,
    tracker: BaseTracker | None = None,
    sector_resolver=None,
    register: bool = False,
    seed: int = DEFAULT_SEED,
    output_dir: str | None = None,
) -> ExperimentReport:
    """Run the full validation experiment. Fails closed unless training is
    enabled in ``settings``.

    Supply ``symbols`` + ``start``/``end`` (provider path) OR
    ``dataset_by_horizon`` (pre-built datasets keyed by horizon, used by tests).
    """
    flags.training_enabled(settings).require()  # raises MLDisabledError if disabled

    horizons = sorted(set(horizons or ([*dataset_by_horizon] if dataset_by_horizon else [5])))
    symbols = [s.upper() for s in (symbols or [])]
    tracker = tracker or get_tracker(settings, fallback_dir=output_dir)
    commit = _git_commit()

    report = ExperimentReport(
        experiment_name=getattr(tracker, "experiment_name", None) or "catalystiq-ml-validation",
        tracking_backend=tracker.backend,
        symbols=symbols,
        benchmark=benchmark,
        direction=direction,
        horizons=horizons,
        date_range=(start.isoformat() if start else None, end.isoformat() if end else None),
        code_commit=commit,
        feature_schema_version=FEATURE_SCHEMA_VERSION,
        target_definition_version=TARGET_DEFINITION_VERSION,
        split_protocol_version=SPLIT_PROTOCOL_VERSION,
    )

    with tracker.run(f"experiment:{report.experiment_name}") as _parent:
        tracker.set_tags({
            "phase": "historical_validation",
            "authorized_process_only": True,
            "serving_enabled": False,
            "approval_performed": False,
        })
        tracker.log_params({
            "symbols": ",".join(symbols),
            "benchmark": benchmark,
            "direction": direction,
            "horizons": ",".join(str(h) for h in horizons),
            "start": start.isoformat() if start else None,
            "end": end.isoformat() if end else None,
            "step_days": step_days,
            "seed": seed,
            "code_commit": commit,
            "feature_contract_version": FEATURE_SCHEMA_VERSION,
            "label_contract_version": TARGET_DEFINITION_VERSION,
            "split_protocol_version": SPLIT_PROTOCOL_VERSION,
            "is_synthetic": is_synthetic_data or bool(dataset_by_horizon),
        })
        tracker.log_dict(_feature_manifest(), "feature_manifest.json")

        for horizon in horizons:
            hz = _run_horizon(
                db, horizon=horizon, symbols=symbols, benchmark=benchmark,
                start=start, end=end, step_days=step_days, direction=direction,
                dataset=(dataset_by_horizon or {}).get(horizon),
                is_synthetic_data=is_synthetic_data, settings=settings,
                tracker=tracker, sector_resolver=sector_resolver, register=register,
                seed=seed, db_out=db,
            )
            report.horizons_results.append(hz)

        # Cross-horizon comparison + final holdout summary at the parent level.
        tracker.log_dict(_model_comparison_report(report), "model_comparison_report.json")
        tracker.log_dict(_final_holdout_report(report), "final_holdout_report.json")

    return report


# ---------------------------------------------------------------------------
# per-horizon orchestration
# ---------------------------------------------------------------------------
def _run_horizon(
    db, *, horizon, symbols, benchmark, start, end, step_days, direction,
    dataset, is_synthetic_data, settings, tracker, sector_resolver, register, seed, db_out,
) -> HorizonResult:
    if dataset is None:
        dataset = _build_dataset(
            db, symbols=symbols, benchmark=benchmark, start=start, end=end,
            step_days=step_days, direction=direction, horizon=horizon,
            is_synthetic_data=is_synthetic_data, sector_resolver=sector_resolver,
        )

    coverage = _feature_coverage(dataset)
    folds = _fold_diagnostics(dataset)
    labels = _label_diagnostics(dataset)
    sufficiency = _assess_sufficiency(dataset, coverage, folds, labels)
    provenance = _provenance_findings(dataset)
    gate_passed = bool(
        sufficiency["sufficient_for_training"]
        and folds.chronology_ok
        and not folds.leakage_findings
        and not provenance["leakage_rejections"]
    )
    split = chronological_split(dataset)
    hz = HorizonResult(
        horizon_days=horizon,
        dataset_size=dataset.size,
        is_synthetic=dataset.is_synthetic,
        dataset_version=dataset.training_data_version(),
        dataset_hash=_dataset_hash(dataset),
        gate_passed=gate_passed,
        gate={
            "sufficiency": sufficiency,
            "chronology_ok": folds.chronology_ok,
            "leakage_findings": folds.leakage_findings,
            "provenance": provenance,
            "coverage": asdict(coverage),
            "labels": asdict(labels),
        },
        split=_split_summary(dataset, split),
    )

    sector_of = sector_resolver or (lambda s: None)

    with tracker.run(f"horizon:{horizon}d", nested=True):
        tracker.log_params({
            "horizon_days": horizon,
            "dataset_version": hz.dataset_version,
            "dataset_hash": hz.dataset_hash,
            "dataset_size": dataset.size,
            "is_synthetic": dataset.is_synthetic,
            "cost_model_version": dataset.cost_model_version,
            "both_touched_policy": dataset.both_touched_policy,
            "purge_protocol": "outcome-window purge + embargo",
            "embargo_days": 5,
            "holdout_fraction": 0.2,
            "calibration_fraction": 0.2,
            "n_walk_forward_folds": folds.n_folds,
            **{f"holdout_boundary_{k}": v for k, v in _boundaries(split).items()},
        })
        tracker.log_metrics({
            "dataset_size": dataset.size,
            "mean_feature_completeness": coverage.mean_completeness,
            "net_profit_positive_rate": labels.net_profit_positive_rate,
            "net_return_std": labels.net_return_std,
            "n_folds": folds.n_folds,
            "develop_purged": folds.develop_purged,
            "fold_purged_total": folds.purged_total,
            "fold_embargoed_total": folds.embargoed_total,
        })
        tracker.log_metrics(coverage.per_group_present_rate, prefix="coverage")
        tracker.set_tags({
            "gate_passed": gate_passed,
            "always_missing_groups": ",".join(coverage.always_missing_groups),
        })
        tracker.log_dict(_dataset_manifest(dataset, symbols, benchmark, direction, horizon, hz),
                         "dataset_manifest.json")
        tracker.log_dict(_provenance_summary(dataset, provenance), "provenance_summary.json")
        tracker.log_dict(hz.gate, "gate_verdict.json")
        tracker.log_dict(_fold_definitions(split), "fold_definitions.json")

        if not gate_passed:
            hz.warnings.append(
                "Training REFUSED for this horizon: sufficiency/chronology/leakage/"
                "provenance/point-in-time checks did not pass. See gate_verdict.json."
            )
            tracker.set_tags({"training_refused": True})
            return hz

        # --- Models 1-3 (purged embargoed walk-forward + final holdout) -----
        m1 = _family_run(tracker, dataset, split, "model_1", horizon, direction, sector_of, folds)
        m2 = _family_run(tracker, dataset, split, "model_2", horizon, direction, sector_of, folds)
        m3 = _family_run(tracker, dataset, split, "model_3", horizon, direction, sector_of, folds)
        hz.models = [m1, m2, m3]

        # --- OOF predictions BEFORE Model 4 --------------------------------
        oof = generate_oof_predictions(dataset, horizon_days=horizon, direction=direction, sector_of=sector_of)
        hz.oof = {
            "coverage_rate": round(oof.coverage_rate, 4),
            "n_predictions": len(oof.predictions),
            "n_validation": len(oof.validation_indices),
            "n_holdout": len(oof.holdout_indices),
            "n_folds": oof.n_folds,
            "holdout_never_predicted": not (set(oof.predictions) & set(oof.holdout_indices)),
            "warnings": oof.warnings[:20],
        }
        with tracker.run("oof:models_1_3", nested=True):
            tracker.log_metrics({
                "oof_coverage_rate": oof.coverage_rate,
                "oof_n_predictions": len(oof.predictions),
                "oof_holdout_never_predicted": 1.0 if hz.oof["holdout_never_predicted"] else 0.0,
            })
            tracker.set_tags({"oof_holdout_isolation_ok": hz.oof["holdout_never_predicted"]})

        # --- Model 4 (ranker) uses OOF inputs only -------------------------
        hz.ranking = _ranking_run(tracker, oof, horizon, direction)

        # --- Model 5 (aggregate response) - separate evidence source -------
        hz.behavior_model = _behavior_run(tracker, dataset, horizon)

        if register:
            _register_candidates(db_out, dataset, hz, direction, horizon)

    return hz


# ---------------------------------------------------------------------------
# Models 1-3
# ---------------------------------------------------------------------------
_TRAINERS = {"model_1": train_model_one, "model_2": train_model_two, "model_3": train_model_three}


def _family_run(tracker, dataset, split, family, horizon, direction, sector_of, folds) -> ModelResult:
    result = ModelResult(family=family, horizon_days=horizon, trained=False)
    with tracker.run(f"{family}:{horizon}d", nested=True):
        trainer = _TRAINERS[family]
        rep = trainer(dataset, horizon_days=horizon, direction=direction)
        result.warnings = list(rep.warnings)
        trained = rep.artifact is not None
        result.trained = trained

        # Per-fold child runs (walk-forward stability) for auditability.
        for f in split.folds:
            with tracker.run(f"{family}:{horizon}d:fold_{f.fold_id}", nested=True):
                tracker.log_metrics({
                    "n_train": len(f.train), "n_calibration": len(f.calibration),
                    "n_validation": len(f.validation), "purged": f.purged_count,
                    "embargoed": f.embargoed_count,
                })
                tracker.set_tags({"fold_id": f.fold_id})

        tracker.log_params({
            "family": family, "horizon_days": horizon, "direction": direction,
            "n_train": rep.split.n_train, "n_calibration": rep.split.n_calibration,
            "n_holdout": rep.split.n_holdout, "n_folds": rep.split.n_folds,
            "holdout_start": rep.split.holdout_start, "seed": DEFAULT_SEED,
        })
        if not trained:
            tracker.set_tags({"trained": False})
            return result

        result.chosen = _chosen_summary(family, rep)
        # candidate-vs-baseline (child runs per candidate algorithm)
        _log_candidate_child_runs(tracker, family, rep)

        # Final holdout evaluation, ONCE, after candidate selection.
        holdout = _holdout_evaluation(tracker, dataset, split, family, rep, sector_of)
        result.holdout_metrics = holdout["metrics"]
        result.scorer_baseline_metrics = holdout.get("scorer_baseline", {})
        result.sliced_metrics = holdout.get("sliced", {})
        result.acceptance = holdout.get("acceptance", {})

        tracker.log_metrics(result.holdout_metrics, prefix="holdout")
        if result.scorer_baseline_metrics:
            tracker.log_metrics(result.scorer_baseline_metrics, prefix="scorer_baseline")
        tracker.set_tags(result.acceptance)
    return result


def _chosen_summary(family, rep) -> dict:
    if family == "model_1":
        return {"net_profit": rep.net_profit.get("chosen"),
                "target_before_stop": rep.target_before_stop.get("chosen")}
    if family == "model_2":
        return {"quantile": rep.quantile.get("chosen")}
    return {"metrics_present": bool(rep.metrics)}


def _log_candidate_child_runs(tracker, family, rep) -> None:
    """One nested run per (head, candidate algorithm) with the baseline-vs-
    candidate comparison, so the choice is auditable per algorithm."""
    heads = []
    if family == "model_1":
        heads = [("net_profit", rep.net_profit), ("target_before_stop", rep.target_before_stop)]
    elif family == "model_2":
        heads = [("net_return_quantile", rep.quantile)]
    elif family == "model_3":
        for k in ("adverse_excursion", "favorable_excursion", "tail_return", "stop_breach", "gap_beyond_stop"):
            if k in rep.metrics:
                heads.append((k, rep.metrics[k]))
    for head_name, head in heads:
        if not isinstance(head, dict) or not head:
            continue
        with tracker.run(f"{family}:{head_name}:candidate", nested=True):
            tracker.set_tags({
                "head": head_name,
                "chosen_algorithm": head.get("chosen"),
                "candidate_approved": head.get("candidate_approved"),
            })
            if head.get("baseline_metrics"):
                tracker.log_metrics(head["baseline_metrics"], prefix="baseline")
            if head.get("candidate_metrics"):
                tracker.log_metrics(head["candidate_metrics"], prefix="candidate")
            elif head.get("metrics"):
                tracker.log_metrics(head["metrics"], prefix="candidate")


# ---------------------------------------------------------------------------
# final holdout evaluation (once, after selection) + plots + slices
# ---------------------------------------------------------------------------
def _holdout_evaluation(tracker, dataset, split, family, rep, sector_of) -> dict:
    artifact = rep.artifact
    ho_idx = split.holdout_idx
    out: dict = {"metrics": {}, "sliced": {}, "acceptance": {}}
    if not ho_idx:
        return out

    if family == "model_1":
        y_true, y_prob, idx = _predict_binary(dataset, ho_idx, artifact,
                                               lambda ex: ex.labels.net_profit_label,
                                               lambda a, X: a.net_profit_head.predict_proba(X),
                                               artifact.feature_names)
        if len(y_true) == 0:
            return out
        metrics = classification_metrics(y_true, y_prob)
        out["metrics"] = metrics
        # deterministic Catalyst IQ scorer baseline on the SAME holdout.
        out["scorer_baseline"] = _scorer_baseline_metrics(dataset, ho_idx,
                                                           lambda ex: ex.labels.net_profit_label)
        out["acceptance"] = _acceptance(metrics, out["scorer_baseline"])
        out["sliced"] = _sliced_classification(dataset, idx, y_true, y_prob, sector_of)
        _log_classification_artifacts(tracker, family, "net_profit", y_true, y_prob, artifact, dataset)
        _log_failure_cases(tracker, family, dataset, idx, y_true, y_prob)
    elif family == "model_2":
        y_true, preds, idx = _predict_quantile(dataset, ho_idx, artifact,
                                               lambda ex: ex.labels.net_terminal_return,
                                               artifact.feature_names)
        if len(y_true) == 0:
            return out
        qm = quantile_metrics(y_true, preds)
        out["metrics"] = qm
        out["acceptance"] = {"meets_acceptance_thresholds": _finite(qm.get("median_mae")),
                             "verdict": "quantile_holdout_evaluated"}
        _log_quantile_artifacts(tracker, family, y_true, preds)
    elif family == "model_3":
        y_true, preds, idx = _predict_quantile(dataset, ho_idx, artifact,
                                               lambda ex: ex.labels.max_adverse_excursion,
                                               artifact.feature_names, head_attr="mae_head")
        if len(y_true):
            out["metrics"] = quantile_metrics(y_true, preds)
            out["acceptance"] = {"verdict": "path_risk_holdout_evaluated"}
            _log_quantile_artifacts(tracker, family, y_true, preds, coverage_title="Model 3 MAE coverage")
    return out


def _predict_binary(dataset, idx, artifact, label_getter, proba_fn, feature_names):
    rows, ys, kept = [], [], []
    for i in idx:
        ex = dataset.examples[i]
        lab = label_getter(ex)
        if lab is None:
            continue
        rows.append(ex.features)
        ys.append(int(lab))
        kept.append(i)
    if not rows:
        return np.array([]), np.array([]), []
    X = to_matrix(rows, feature_names)
    probs = proba_fn(artifact, X)
    return np.asarray(ys, float), np.asarray(probs, float), kept


def _predict_quantile(dataset, idx, artifact, label_getter, feature_names, head_attr=None):
    rows, ys, kept = [], [], []
    for i in idx:
        ex = dataset.examples[i]
        lab = label_getter(ex)
        if lab is None:
            continue
        rows.append(ex.features)
        ys.append(float(lab))
        kept.append(i)
    if not rows:
        return np.array([]), {}, []
    X = to_matrix(rows, feature_names)
    head = getattr(artifact, head_attr) if head_attr else artifact.head
    preds = {k: np.asarray(v, float) for k, v in head.predict(X).items()}
    return np.asarray(ys, float), preds, kept


def _scorer_baseline_metrics(dataset, idx, label_getter) -> dict:
    """Deterministic Catalyst IQ rule-based scorer as a probability-like
    baseline on the holdout: rule_based_setup_strength (0..100) -> [0,1]."""
    ys, scores = [], []
    for i in idx:
        ex = dataset.examples[i]
        lab = label_getter(ex)
        s = ex.features.get("rule_based_setup_strength")
        if lab is None or s is None:
            continue
        ys.append(int(lab))
        scores.append(max(0.0, min(1.0, float(s) / 100.0)))
    if len(set(ys)) < 2:
        return {"available": 0.0, "reason_no_variety": 1.0}
    m = classification_metrics(ys, scores)
    m["available"] = 1.0
    return m


def _acceptance(candidate_metrics: dict, scorer_metrics: dict) -> dict:
    auc = candidate_metrics.get("roc_auc")
    ece = candidate_metrics.get("expected_calibration_error")
    beats_scorer = None
    if scorer_metrics.get("available") == 1.0 and _finite(scorer_metrics.get("roc_auc")):
        beats_scorer = _finite(auc) and float(auc) > float(scorer_metrics["roc_auc"])
    meets = _finite(auc) and float(auc) >= MIN_HOLDOUT_AUC and _finite(ece) and float(ece) <= MAX_HOLDOUT_ECE
    return {
        "meets_acceptance_thresholds": bool(meets),
        "beats_deterministic_scorer": beats_scorer,
        "min_holdout_auc": MIN_HOLDOUT_AUC,
        "max_holdout_ece": MAX_HOLDOUT_ECE,
        "verdict": "meets_thresholds" if meets else "below_thresholds",
    }


def _sliced_classification(dataset, idx, y_true, y_prob, sector_of) -> dict:
    """Holdout metrics sliced by sector, market regime and confidence band."""
    by_sector: dict[str, list[int]] = {}
    by_regime: dict[str, list[int]] = {}
    by_band: dict[str, list[int]] = {}
    for pos, i in enumerate(idx):
        ex = dataset.examples[i]
        sec = sector_of(ex.symbol) or "unknown"
        reg = _regime_label(ex.features.get("market_regime"))
        band = _confidence_band(y_prob[pos])
        by_sector.setdefault(sec, []).append(pos)
        by_regime.setdefault(reg, []).append(pos)
        by_band.setdefault(band, []).append(pos)
    return {
        "by_sector": _slice_metrics(y_true, y_prob, by_sector),
        "by_market_regime": _slice_metrics(y_true, y_prob, by_regime),
        "by_confidence_band": _slice_metrics(y_true, y_prob, by_band),
    }


def _slice_metrics(y_true, y_prob, groups) -> dict:
    out = {}
    for key, positions in groups.items():
        yt = y_true[positions]
        yp = y_prob[positions]
        if len(yt) == 0:
            continue
        out[key] = {
            "n": float(len(yt)),
            "positive_rate": float(np.mean(yt)),
            "mean_predicted": float(np.mean(yp)),
            "brier_score": float(np.mean((yp - yt) ** 2)),
        }
    return out


def _log_classification_artifacts(tracker, family, head, y_true, y_prob, artifact, dataset) -> None:
    prefix = f"{family}/{head}"
    bins = reliability_bins(y_prob, y_true)
    tracker.log_dict([asdict(b) for b in bins], f"{prefix}/reliability_bins.json")
    _log_fig(tracker, plots.calibration_plot(bins, title=f"{prefix} calibration"),
             f"{prefix}/calibration.png")
    _log_fig(tracker, plots.roc_curve_plot(y_true, y_prob, title=f"{prefix} ROC"), f"{prefix}/roc.png")
    _log_fig(tracker, plots.pr_curve_plot(y_true, y_prob, title=f"{prefix} PR"), f"{prefix}/pr.png")
    _log_fig(tracker, plots.confusion_matrix_plot(y_true, y_prob, title=f"{prefix} confusion"),
             f"{prefix}/confusion_matrix.png")
    imp = _feature_importance(artifact, head)
    if imp is not None:
        names, vals = imp
        tracker.log_dict(dict(zip(names, [float(v) for v in vals])), f"{prefix}/feature_importance.json")
        _log_fig(tracker, plots.feature_importance_plot(names, vals, title=f"{prefix} importance"),
                 f"{prefix}/feature_importance.png")


def _log_quantile_artifacts(tracker, family, y_true, preds, coverage_title=None) -> None:
    qm = quantile_metrics(y_true, preds)
    tracker.log_dict(qm, f"{family}/quantile_metrics.json")
    _log_fig(tracker, plots.quantile_coverage_plot(qm.get("coverage", {}),
             title=coverage_title or f"{family} quantile coverage"), f"{family}/quantile_coverage.png")
    if "q50" in preds:
        _log_fig(tracker, plots.predicted_vs_actual_plot(y_true, preds["q50"],
                 title=f"{family} predicted vs actual (median)"), f"{family}/predicted_vs_actual.png")


def _log_failure_cases(tracker, family, dataset, idx, y_true, y_prob, top=25) -> None:
    """Worst-calibrated holdout errors (largest |prob - label|)."""
    errs = np.abs(y_prob - y_true)
    order = np.argsort(-errs)[:top]
    cases = []
    for o in order:
        ex = dataset.examples[idx[o]]
        cases.append({
            "symbol": ex.symbol,
            "prediction_timestamp": ex.prediction_timestamp.isoformat(),
            "label": int(y_true[o]),
            "predicted_probability": round(float(y_prob[o]), 4),
            "abs_error": round(float(errs[o]), 4),
            "net_terminal_return": ex.labels.net_terminal_return,
        })
    tracker.log_dict({"family": family, "worst_holdout_cases": cases}, f"{family}/failure_cases.json")


def _feature_importance(artifact, head):
    """Legitimate feature importance only when the chosen head is a GBDT
    candidate exposing ``feature_importances_``."""
    est = None
    if head in ("net_profit",) and hasattr(artifact, "net_profit_head"):
        est = getattr(artifact.net_profit_head, "_estimator", None)
        names = artifact.net_profit_head.preprocessor.feature_names
    else:
        return None
    if est is None or not hasattr(est, "feature_importances_"):
        return None
    return list(names), list(est.feature_importances_)


# ---------------------------------------------------------------------------
# Model 4 - ranking (OOF inputs only) + trading metrics after costs
# ---------------------------------------------------------------------------
def _ranking_run(tracker, oof, horizon, direction) -> dict:
    examples, ranked_items = _build_ranker_examples(oof)
    with tracker.run(f"model_4:{horizon}d", nested=True):
        tracker.log_params({
            "family": "model_4", "horizon_days": horizon, "direction": direction,
            "n_ranker_examples": len(examples),
            "all_inputs_out_of_fold": all(e.oof for e in examples),
            "ranker_target_version": "realized_utility_v1",
        })
        rep = train_model_four(examples)
        metrics = ranking_metrics(ranked_items) if ranked_items else {}
        trading = _trading_metrics(ranked_items)
        tracker.log_metrics(metrics, prefix="ranking")
        tracker.log_metrics(trading, prefix="trading")
        if rep.ranking_metrics:
            tracker.log_metrics(rep.ranking_metrics, prefix="ranker")
        tracker.set_tags({
            "chosen": rep.chosen, "candidate_approved": rep.candidate_approved,
            "oof_enforced": True,
        })
        tracker.log_dict({"warnings": rep.warnings, "chosen": rep.chosen,
                          "candidate_approved": rep.candidate_approved,
                          "ranking_metrics": metrics, "trading_metrics_after_costs": trading},
                         "model_4/ranking_report.json")
    return {
        "chosen": rep.chosen,
        "candidate_approved": rep.candidate_approved,
        "n_examples": len(examples),
        "all_inputs_out_of_fold": all(e.oof for e in examples),
        "ranking_metrics": metrics,
        "trading_metrics_after_costs": trading,
        "warnings": rep.warnings,
    }


def _build_ranker_examples(oof):
    """Build chronologically-ordered Model 4 examples from OOF Model 1-3 outputs.
    Every input is out-of-fold (``oof=True``); the target is realized utility
    computed under the same executable-entry/cost conventions."""
    usable = [
        p for p in oof.predictions.values()
        if p.net_profit_prob is not None and p.net_terminal_return is not None
    ]
    usable.sort(key=lambda p: p.prediction_timestamp)
    examples: list[RankerExample] = []
    ranked_items: list[RankedItem] = []
    for p in usable:
        inp = OpportunityInputs(
            symbol=p.symbol,
            net_profit_probability=_clip01(p.net_profit_prob),
            target_before_stop_probability=_clip01(p.target_before_stop_prob, 0.5),
            normalized_median_net_return=float(p.median_net_return or 0.0),
            normalized_rule_based_setup_strength=_clip01((p.rule_based_setup_strength or 0.0) / 100.0),
            model_reliability=_reliability_proxy(p),
            sector_and_market_alignment=_clip01(p.market_sector_alignment, 0.5),
            severe_downside=float(p.severe_adverse_excursion if p.severe_adverse_excursion is not None
                                  else (p.max_adverse_excursion or 0.0)),
            stop_breach_probability=_clip01(p.stop_breach_prob, 0.0),
            gap_risk_probability=0.0,  # not estimable OOF at this sample size; never fabricated
            liquidity_cost_penalty=_clip01(p.liquidity_cost_penalty, 0.0),
            sector=p.sector,
        )
        util = realized_utility(
            net_terminal_return=float(p.net_terminal_return),
            severe_downside=float(p.max_adverse_excursion or 0.0),
            stop_breach=int(p.stop_breach_label or 0),
            gap_risk=int(p.gap_beyond_stop_label or 0),
            transaction_cost=float(p.round_trip_cost or 0.0),
        )
        examples.append(RankerExample(inputs=inp, realized_utility=util, oof=True))
        ranked_items.append(RankedItem(
            symbol=p.symbol,
            predicted_score=baseline_opportunity_utility(inp, DEFAULT_RANKER_WEIGHTS),
            realized_net_return=float(p.net_terminal_return),
            is_good=bool(p.net_profit_label),
            sector=p.sector,
        ))
    return examples, ranked_items


def _trading_metrics(ranked_items) -> dict:
    """Realized, after-cost trading metrics for the top-ranked candidates (the
    net returns already have spread+slippage+fees+impact subtracted)."""
    if not ranked_items:
        return {}
    out = {}
    from catalystiq.ml.evaluation.ranking import top_k_returns
    for k in (1, 4, 10):
        mean_r, med_r = top_k_returns(ranked_items, k)
        out[f"mean_net_return_top_{k}_after_costs"] = mean_r
        out[f"median_net_return_top_{k}_after_costs"] = med_r
    rets = np.array([it.realized_net_return for it in ranked_items], dtype=float)
    out["universe_mean_net_return_after_costs"] = float(np.mean(rets))
    out["universe_hit_rate"] = float(np.mean(rets > 0))
    return out


# ---------------------------------------------------------------------------
# Model 5 - aggregate response (separate evidence source)
# ---------------------------------------------------------------------------
def _behavior_run(tracker, dataset, horizon) -> dict:
    from catalystiq.ml.models.model_five import (
        MIN_COMPARABLES,
        AntecedentType,
        MarketStateSnapshot,
        detect_antecedents,
    )

    counts: dict[str, int] = {}
    pos_follow: dict[str, int] = {}
    for ex in dataset.examples:
        feats = ex.features
        state = MarketStateSnapshot(
            symbol=ex.symbol,
            as_of=ex.prediction_timestamp.isoformat(),
            overnight_gap_pct=_f(feats.get("overnight_gap_pct")),
            relative_volume=_f(feats.get("relative_volume_20d")),
        )
        dets = detect_antecedents(state)
        good = (ex.labels.net_terminal_return or 0.0) > 0
        for d in dets:
            counts[d.type.value] = counts.get(d.type.value, 0) + 1
            if good:
                pos_follow[d.type.value] = pos_follow.get(d.type.value, 0) + 1

    tables = {
        t: {
            "count": counts[t],
            "positive_follow_through_rate": (pos_follow.get(t, 0) / counts[t]) if counts[t] else 0.0,
            "sufficient_comparables": counts[t] >= MIN_COMPARABLES,
        }
        for t in counts
    }
    with tracker.run(f"model_5:{horizon}d", nested=True):
        tracker.log_params({"family": "model_5", "min_comparables": MIN_COMPARABLES,
                            "note": "separate evidence source; never alters Models 1-4"})
        tracker.log_metrics({f"antecedent_count.{t}": c for t, c in counts.items()})
        tracker.log_dict({"antecedent_tables": tables}, "model_5/aggregate_response_tables.json")
        tracker.set_tags({"n_antecedent_types_detected": len(counts)})
    return {"antecedent_tables": tables, "n_types": len(counts)}


# ---------------------------------------------------------------------------
# dataset build (provider path)
# ---------------------------------------------------------------------------
def _build_dataset(db, *, symbols, benchmark, start, end, step_days, direction,
                   horizon, is_synthetic_data, sector_resolver) -> TrainingDataset:
    provider = SilverPointInTimeProvider(
        db, benchmark_symbol=benchmark, sector_resolver=sector_resolver or (lambda s: None),
    )
    builder = TrainingExampleBuilder(
        provider, for_training=True, is_synthetic=is_synthetic_data,
        source_providers=["computed", "sec_edgar", "bls", "bea"],
    )
    dates = _prediction_dates(start, end, step_days)
    requests = [ExampleRequest(sym, ts, direction, horizon) for sym in symbols for ts in dates]
    return builder.build(requests)


def _prediction_dates(start: dt.date, end: dt.date, step_days: int) -> list[dt.datetime]:
    if start is None or end is None:
        raise ValueError("provider path requires start and end dates")
    if end < start:
        raise ValueError("end must be on or after start")
    out: list[dt.datetime] = []
    d = start
    step = max(1, step_days)
    while d <= end:
        out.append(dt.datetime(d.year, d.month, d.day, 20, 0, 0))
        d += dt.timedelta(days=step)
    return out


# ---------------------------------------------------------------------------
# candidate registration (never approved)
# ---------------------------------------------------------------------------
def _register_candidates(db, dataset, hz: HorizonResult, direction, horizon) -> None:
    if db is None:
        return
    from catalystiq.ml import registry
    families = [m.family for m in hz.models if m.trained]
    split = chronological_split(dataset)
    boundaries = _boundaries(split)
    for fam in families:
        try:
            registry.register_artifact(
                db,
                registry.ArtifactSpec(
                    model_name=f"experiment_{fam}_{direction}_{horizon}d",
                    model_version="experiment-1",
                    model_family=fam,
                    horizon_days=horizon,
                    trade_direction=direction,
                    feature_schema_version=FEATURE_SCHEMA_VERSION,
                    target_definition_version=TARGET_DEFINITION_VERSION,
                    training_data_version=dataset.training_data_version(),
                    is_synthetic=dataset.is_synthetic,
                    evaluation_metrics={"experiment": True},
                    notes="experiment candidate; never eligible for approval by this runner",
                    holdout_start=_parse_dt(boundaries.get("holdout_start")),
                ),
            )
        except Exception as exc:  # pragma: no cover - best effort
            hz.warnings.append(f"candidate registration failed for {fam}: {exc}")


# ---------------------------------------------------------------------------
# manifests / reports / helpers
# ---------------------------------------------------------------------------
def _feature_manifest() -> dict:
    groups: dict[str, list[str]] = {}
    for name, spec in FEATURE_CATALOG.items():
        groups.setdefault(spec.group.value, []).append(name)
    return {
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "n_features": len(FEATURE_CATALOG),
        "groups": {g: sorted(v) for g, v in sorted(groups.items())},
        "note": ("earnings_proximity is intentionally unavailable (no licensed, "
                 "timestamped feed); it is recorded as missing, never fabricated."),
    }


def _dataset_manifest(dataset, symbols, benchmark, direction, horizon, hz) -> dict:
    start, end = dataset.date_coverage
    return {
        "dataset_version": dataset.training_data_version(),
        "dataset_hash": hz.dataset_hash,
        "is_synthetic": dataset.is_synthetic,
        "symbols": symbols,
        "benchmark": benchmark,
        "direction": direction,
        "horizon_days": horizon,
        "size": dataset.size,
        "date_coverage": [start.isoformat() if start else None, end.isoformat() if end else None],
        "feature_schema_version": dataset.feature_schema_version,
        "target_definition_version": dataset.target_definition_version,
        "cost_model_version": dataset.cost_model_version,
        "both_touched_policy": dataset.both_touched_policy,
        "source_providers": dataset.source_providers,
        "skipped_requests": len(dataset.skipped),
    }


def _provenance_findings(dataset) -> dict:
    leakage = 0
    licensing = 0
    provenance = 0
    for ex in dataset.examples:
        for rej in ex.feature_rejections:
            if rej.code == "leakage":
                leakage += 1
            elif rej.code == "licensing":
                licensing += 1
            elif rej.code == "provenance":
                provenance += 1
    gaps = sorted({g for ex in dataset.examples for g in ex.requirement_gaps})
    return {
        "leakage_rejections": leakage,
        "licensing_rejections": licensing,
        "provenance_rejections": provenance,
        "requirement_gaps": gaps,
    }


def _provenance_summary(dataset, provenance) -> dict:
    return {
        "point_in_time": True,
        "look_ahead_invariant": True,
        "executable_next_open_entry": True,
        "costs_subtracted": ["spread", "slippage", "fees", "market_impact"],
        "cost_model_version": dataset.cost_model_version,
        "both_touched_policy": dataset.both_touched_policy,
        "feature_rejections": provenance,
        "known_gaps": provenance["requirement_gaps"],
    }


def _split_summary(dataset, split) -> dict:
    return {
        "n_train": len(split.train_idx),
        "n_calibration": len(split.calib_idx),
        "n_holdout": len(split.holdout_idx),
        "n_folds": len(split.folds),
        "develop_purged": split.develop_purged,
        **_boundaries(split),
    }


def _boundaries(split) -> dict:
    return {
        "holdout_start": split.holdout_start.isoformat() if split.holdout_start else None,
    }


def _fold_definitions(split) -> dict:
    return {
        "holdout_start": split.holdout_start.isoformat() if split.holdout_start else None,
        "holdout_indices": list(split.holdout_idx),
        "train_indices": list(split.train_idx),
        "calibration_indices": list(split.calib_idx),
        "folds": [
            {
                "fold_id": f.fold_id,
                "n_train": len(f.train), "n_calibration": len(f.calibration),
                "n_validation": len(f.validation),
                "purged": f.purged_count, "embargoed": f.embargoed_count,
                "validation_span": [s.isoformat() for s in f.validation_span] if f.validation_span else None,
            }
            for f in split.folds
        ],
    }


def _model_comparison_report(report: ExperimentReport) -> dict:
    rows = []
    for hz in report.horizons_results:
        for m in hz.models:
            rows.append({
                "horizon_days": hz.horizon_days,
                "family": m.family,
                "trained": m.trained,
                "chosen": m.chosen,
                "holdout_roc_auc": m.holdout_metrics.get("roc_auc"),
                "holdout_pr_auc": m.holdout_metrics.get("pr_auc"),
                "holdout_brier": m.holdout_metrics.get("brier_score"),
                "holdout_ece": m.holdout_metrics.get("expected_calibration_error"),
                "scorer_roc_auc": m.scorer_baseline_metrics.get("roc_auc"),
                "acceptance": m.acceptance,
            })
        rows.append({"horizon_days": hz.horizon_days, "family": "model_4",
                     "ranking": hz.ranking.get("ranking_metrics", {}),
                     "trading_after_costs": hz.ranking.get("trading_metrics_after_costs", {}),
                     "chosen": hz.ranking.get("chosen")})
    return {"experiment": report.experiment_name, "comparison": rows}


def _final_holdout_report(report: ExperimentReport) -> dict:
    return {
        "note": ("Final holdout evaluated ONCE per model after candidate selection. "
                 "No serving, approval or promotion is performed by this runner."),
        "horizons": [
            {
                "horizon_days": hz.horizon_days,
                "gate_passed": hz.gate_passed,
                "models": [
                    {"family": m.family, "trained": m.trained,
                     "holdout_metrics": m.holdout_metrics,
                     "scorer_baseline_metrics": m.scorer_baseline_metrics,
                     "sliced_metrics": m.sliced_metrics,
                     "acceptance": m.acceptance}
                    for m in hz.models
                ],
                "ranking": hz.ranking,
                "behavior_model": hz.behavior_model,
            }
            for hz in report.horizons_results
        ],
    }


def _dataset_hash(dataset) -> str:
    h = hashlib.sha256()
    for ex in dataset.examples:
        h.update(f"{ex.symbol}|{ex.prediction_timestamp.isoformat()}|{ex.horizon_days}|".encode())
        h.update(f"{ex.labels.net_profit_label}|{ex.labels.net_terminal_return}".encode())
    return h.hexdigest()[:32]


def _git_commit() -> str | None:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], stderr=subprocess.DEVNULL, timeout=5,
        )
        return out.decode().strip()
    except Exception:
        return None


def _log_fig(tracker, fig, artifact_file) -> None:
    if fig is None:
        return
    try:
        tracker.log_figure(fig, artifact_file)
    finally:
        try:
            import matplotlib.pyplot as plt
            plt.close(fig)
        except Exception:
            pass


def _regime_label(code) -> str:
    if code is None:
        return "unknown"
    try:
        return f"regime_{int(code)}"
    except (TypeError, ValueError):
        return str(code)


def _confidence_band(prob: float) -> str:
    if prob < 0.4:
        return "low"
    if prob < 0.6:
        return "medium"
    return "high"


def _reliability_proxy(p) -> float:
    """0..1 reliability proxy from OOF completeness (how many M1-3 outputs are
    present for this example). Not a probability - a coverage proxy."""
    present = sum(1 for v in (p.net_profit_prob, p.target_before_stop_prob,
                              p.median_net_return, p.stop_breach_prob) if v is not None)
    return present / 4.0


def _clip01(v, default=0.0) -> float:
    if v is None:
        return float(default)
    return float(max(0.0, min(1.0, v)))


def _f(v):
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def _finite(v) -> bool:
    try:
        return v is not None and np.isfinite(float(v))
    except (TypeError, ValueError):
        return False


def _parse_dt(s):
    if not s:
        return None
    try:
        return dt.datetime.fromisoformat(s)
    except (TypeError, ValueError):
        return None
