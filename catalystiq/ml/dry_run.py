"""Chronological training dry-run harness (offline diagnostics).

Exercises the whole offline path end-to-end on whatever validated Silver data
exists, WITHOUT approving anything or serving a prediction:

    SilverPointInTimeProvider -> TrainingExampleBuilder -> chronological split
    + purged walk-forward + leakage checks -> feature-coverage & label
    diagnostics -> (optionally) fit candidate Models 1-3 -> sufficiency verdict.

Its job is to answer "are the wired point-in-time features and the available
history actually sufficient to train?" before any model is approved. It is a
TRAINING-side tool and FAILS CLOSED: it refuses to run unless training is
enabled (``ENABLE_ML`` + ``ENABLE_ML_TRAINING``) via the passed settings.
Enabling those in a deliberate offline/test settings object does not enable
inference, serving, or approval - candidate metrics are reported, never
promoted.

Model fitting additionally requires scikit-learn; if it is absent the harness
still returns the dataset/split/coverage diagnostics and records why fitting
was skipped. Nothing here writes to the model registry unless the caller
passes ``register=True`` (and even then only as ``candidate``).
"""
from __future__ import annotations

import datetime as dt
from dataclasses import asdict, dataclass, field

from catalystiq.config import Settings
from catalystiq.ml import flags
from catalystiq.ml.dataset.builder import ExampleRequest, TrainingDataset, TrainingExampleBuilder
from catalystiq.ml.features.pit_provider import SilverPointInTimeProvider
from catalystiq.ml.features.schema import FEATURE_CATALOG, missing_indicator_name
from catalystiq.ml.models.base import sklearn_available
from catalystiq.ml.models.training import chronological_split, SplitReport
from catalystiq.ml.validation.leakage import (
    assert_chronological_fold,
    check_outcome_window_purge,
)

# Sufficiency thresholds (documented, tunable). These are gates for a *dry run*
# verdict, NOT model-approval criteria.
MIN_EXAMPLES = 300
MIN_COMPLETENESS = 0.5
MIN_CLASS_RATE = 0.05
MAX_CLASS_RATE = 0.95


@dataclass
class FeatureCoverage:
    total_catalog_features: int
    mean_completeness: float
    per_group_present_rate: dict[str, float]
    always_missing_groups: list[str]


@dataclass
class FoldDiagnostics:
    n_folds: int
    purged_total: int
    embargoed_total: int
    chronology_ok: bool
    leakage_findings: list[str]
    develop_purged: int


@dataclass
class LabelDiagnostics:
    net_profit_labeled: int
    net_profit_positive_rate: float
    target_before_stop_labeled: int
    net_return_labeled: int
    net_return_std: float


@dataclass
class ModelDiagnostics:
    trained: bool
    skipped_reason: str | None = None
    model_one: dict | None = None
    model_two: dict | None = None
    model_three: dict | None = None


@dataclass
class DryRunReport:
    symbols: list[str]
    direction: str
    horizon_days: int
    dataset_size: int
    date_coverage: tuple[str | None, str | None]
    is_synthetic: bool
    training_data_version: str
    coverage: FeatureCoverage
    folds: FoldDiagnostics
    labels: LabelDiagnostics
    models: ModelDiagnostics
    sufficiency: dict
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def run_training_dry_run(
    db=None,
    *,
    symbols: list[str] | None = None,
    prediction_timestamps: list[dt.datetime] | None = None,
    dataset: TrainingDataset | None = None,
    direction: str = "long",
    horizon_days: int = 5,
    benchmark: str = "SPY",
    sector_resolver=None,
    is_synthetic_data: bool = False,
    settings: Settings | None = None,
    register: bool = False,
    min_examples_to_fit: int = 60,
) -> DryRunReport:
    """Run the dry-run. Fails closed unless training is enabled in ``settings``.

    Either supply ``dataset`` (dry-run an already-built dataset) or ``symbols``
    + ``prediction_timestamps`` (build one from validated Silver via the
    point-in-time provider).
    """
    flags.training_enabled(settings).require()  # raises MLDisabledError if disabled

    if dataset is None:
        if not symbols or not prediction_timestamps:
            raise ValueError("provide either `dataset` or `symbols` + `prediction_timestamps`")
        provider = SilverPointInTimeProvider(
            db, benchmark_symbol=benchmark, sector_resolver=sector_resolver or (lambda s: None)
        )
        builder = TrainingExampleBuilder(
            provider, for_training=True, is_synthetic=is_synthetic_data,
            source_providers=["computed", "sec_edgar", "bls", "bea"],
        )
        requests = [
            ExampleRequest(sym, ts, direction, horizon_days)
            for sym in symbols
            for ts in prediction_timestamps
        ]
        dataset = builder.build(requests)
    symbols = symbols or sorted({ex.symbol for ex in dataset.examples})

    warnings: list[str] = []
    coverage = _feature_coverage(dataset)
    folds = _fold_diagnostics(dataset)
    labels = _label_diagnostics(dataset)
    models = _fit_models(dataset, horizon_days, direction, settings, warnings, min_examples_to_fit)

    start, end = dataset.date_coverage
    sufficiency = _assess_sufficiency(dataset, coverage, folds, labels)

    if dataset.skipped:
        warnings.append(f"{len(dataset.skipped)} example request(s) skipped (no entry/path).")

    report = DryRunReport(
        symbols=list(symbols),
        direction=direction,
        horizon_days=horizon_days,
        dataset_size=dataset.size,
        date_coverage=(start.isoformat() if start else None, end.isoformat() if end else None),
        is_synthetic=dataset.is_synthetic,
        training_data_version=dataset.training_data_version(),
        coverage=coverage,
        folds=folds,
        labels=labels,
        models=models,
        sufficiency=sufficiency,
        warnings=warnings,
    )

    if register and models.trained:
        _register_candidates(db, dataset, report, settings, warnings)

    return report


# --- diagnostics ------------------------------------------------------------
def _feature_coverage(dataset: TrainingDataset) -> FeatureCoverage:
    groups: dict[str, list[str]] = {}
    for name, spec in FEATURE_CATALOG.items():
        groups.setdefault(spec.group.value, []).append(name)

    n = max(1, dataset.size)
    # present rate per catalog feature via its missing indicator (0 = present).
    present_rate: dict[str, float] = {}
    for name in FEATURE_CATALOG:
        ind = missing_indicator_name(name)
        present = sum(
            1 for ex in dataset.examples if ex.features.get(ind) == 0
        )
        present_rate[name] = present / n

    per_group: dict[str, float] = {}
    for g, names in groups.items():
        per_group[g] = round(sum(present_rate[nm] for nm in names) / len(names), 4)

    always_missing = sorted(g for g, r in per_group.items() if r == 0.0)

    completeness_vals = [
        ex.features.get("feature_completeness") for ex in dataset.examples
        if ex.features.get("feature_completeness") is not None
    ]
    mean_completeness = round(sum(completeness_vals) / len(completeness_vals), 4) if completeness_vals else 0.0

    return FeatureCoverage(
        total_catalog_features=len(FEATURE_CATALOG),
        mean_completeness=mean_completeness,
        per_group_present_rate=dict(sorted(per_group.items())),
        always_missing_groups=always_missing,
    )


def _fold_diagnostics(dataset: TrainingDataset) -> FoldDiagnostics:
    split = chronological_split(dataset)
    windows = _windows_by_index(dataset)
    findings: list[str] = []
    chronology_ok = True
    purged = embargoed = 0
    for fold in split.folds:
        purged += fold.purged_count
        embargoed += fold.embargoed_count
        purge_rep = check_outcome_window_purge(fold, windows)
        chrono_rep = assert_chronological_fold(fold, windows)
        if not purge_rep.ok:
            findings.extend(purge_rep.findings)
        if not chrono_rep.ok:
            chronology_ok = False
            findings.extend(chrono_rep.findings)
    return FoldDiagnostics(
        n_folds=len(split.folds),
        purged_total=purged,
        embargoed_total=embargoed,
        chronology_ok=chronology_ok,
        leakage_findings=findings,
        develop_purged=split.develop_purged,
    )


def _windows_by_index(dataset: TrainingDataset):
    from catalystiq.ml.models.training import _windows

    return {w.index: w for w in _windows(dataset.examples)}


def _label_diagnostics(dataset: TrainingDataset) -> LabelDiagnostics:
    np_labels = [ex.labels.net_profit_label for ex in dataset.examples if ex.labels.net_profit_label is not None]
    tbs = [ex.labels.target_before_stop_label for ex in dataset.examples if ex.labels.target_before_stop_label is not None]
    rets = [ex.labels.net_terminal_return for ex in dataset.examples if ex.labels.net_terminal_return is not None]
    pos_rate = (sum(np_labels) / len(np_labels)) if np_labels else float("nan")
    if len(rets) >= 2:
        mean = sum(rets) / len(rets)
        std = (sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)) ** 0.5
    else:
        std = 0.0
    return LabelDiagnostics(
        net_profit_labeled=len(np_labels),
        net_profit_positive_rate=round(pos_rate, 4) if pos_rate == pos_rate else float("nan"),
        target_before_stop_labeled=len(tbs),
        net_return_labeled=len(rets),
        net_return_std=round(std, 6),
    )


def _fit_models(dataset, horizon_days, direction, settings, warnings, min_examples_to_fit=60) -> ModelDiagnostics:
    if not sklearn_available():
        return ModelDiagnostics(trained=False, skipped_reason="scikit-learn not installed")
    if dataset.size < min_examples_to_fit:
        return ModelDiagnostics(trained=False, skipped_reason=f"dataset too small ({dataset.size})")
    from catalystiq.ml.models.model_one import train_model_one
    from catalystiq.ml.models.model_two import train_model_two
    from catalystiq.ml.models.model_three import train_model_three

    m1 = train_model_one(dataset, horizon_days=horizon_days, direction=direction)
    m2 = train_model_two(dataset, horizon_days=horizon_days, direction=direction)
    m3 = train_model_three(dataset, horizon_days=horizon_days, direction=direction)
    for r in (m1, m2, m3):
        warnings.extend(r.warnings)
    return ModelDiagnostics(
        trained=any(r.artifact is not None for r in (m1, m2, m3)),
        model_one={"trained": m1.artifact is not None, "net_profit": m1.net_profit,
                   "target_before_stop": m1.target_before_stop, "split": asdict(m1.split)},
        model_two={"trained": m2.artifact is not None, "quantile": m2.quantile},
        model_three={"trained": m3.artifact is not None, "metrics": m3.metrics},
    )


def _assess_sufficiency(dataset, coverage, folds, labels) -> dict:
    notes: list[str] = []
    enough = dataset.size >= MIN_EXAMPLES
    if not enough:
        notes.append(f"Only {dataset.size} examples (< {MIN_EXAMPLES}); expand symbols/dates or history.")
    completeness_ok = coverage.mean_completeness >= MIN_COMPLETENESS
    if not completeness_ok:
        notes.append(f"Mean feature completeness {coverage.mean_completeness} < {MIN_COMPLETENESS}.")
    pr = labels.net_profit_positive_rate
    balance_ok = pr == pr and MIN_CLASS_RATE < pr < MAX_CLASS_RATE
    if not balance_ok:
        notes.append(f"Net-profit class rate {pr} outside [{MIN_CLASS_RATE},{MAX_CLASS_RATE}].")
    variance_ok = labels.net_return_std > 0
    folds_ok = folds.n_folds >= 1 and folds.chronology_ok and not folds.leakage_findings
    if not folds_ok:
        notes.append("Chronological folds insufficient or leakage detected.")
    if coverage.always_missing_groups:
        notes.append("Always-missing feature groups (expected gaps): "
                     + ", ".join(coverage.always_missing_groups))
    overall = enough and completeness_ok and balance_ok and variance_ok and folds_ok
    return {
        "sufficient_for_training": overall,
        "enough_examples": enough,
        "completeness_ok": completeness_ok,
        "class_balance_ok": balance_ok,
        "return_variance_ok": variance_ok,
        "folds_ok": folds_ok,
        "notes": notes,
    }


def _register_candidates(db, dataset, report: DryRunReport, settings, warnings) -> None:
    """Register the fitted models as CANDIDATE artifacts (never approved). Only
    reached when register=True and models trained."""
    from catalystiq.ml import registry
    from catalystiq.ml import FEATURE_SCHEMA_VERSION, TARGET_DEFINITION_VERSION

    now = _now_from_dataset(dataset)
    families = []
    if report.models.model_one and report.models.model_one.get("trained"):
        families.append("model_1")
    if report.models.model_two and report.models.model_two.get("trained"):
        families.append("model_2")
    if report.models.model_three and report.models.model_three.get("trained"):
        families.append("model_3")
    for fam in families:
        try:
            registry.register_artifact(
                db,
                registry.ArtifactSpec(
                    model_name=f"dryrun_{fam}_{report.direction}_{report.horizon_days}d",
                    model_version="dryrun-1",
                    model_family=fam,
                    horizon_days=report.horizon_days,
                    trade_direction=report.direction,
                    feature_schema_version=FEATURE_SCHEMA_VERSION,
                    target_definition_version=TARGET_DEFINITION_VERSION,
                    training_data_version=report.training_data_version,
                    is_synthetic=dataset.is_synthetic,
                    evaluation_metrics={"dry_run": True},
                    notes="dry-run candidate; never eligible for approval",
                ),
                now=now,
            )
        except Exception as exc:  # pragma: no cover - registration is best-effort
            warnings.append(f"candidate registration failed for {fam}: {exc}")


def _now_from_dataset(dataset) -> dt.datetime:
    _, end = dataset.date_coverage
    return end or dt.datetime(2000, 1, 1)
