"""Out-of-fold (OOF) prediction generation for Models 1-3.

Model 4 (the cross-sectional ranker) must be trained on *historical* Model 1-3
predictions that were generated OUT-OF-FOLD - never in-sample - or it learns
from unrealistically accurate inputs and leaks. This module produces exactly
those OOF predictions by walking the purged, embargoed walk-forward folds from
:func:`catalystiq.ml.models.training.chronological_split` and, for each fold,
training the *existing* Model 1-3 heads on that fold's train slice and
predicting only that fold's validation slice.

It deliberately reuses the existing head trainers
(:func:`train_binary_head`, :func:`train_quantile_head`) rather than
re-implementing any model. The untouched final holdout is NEVER predicted here,
so OOF generation can never contaminate the holdout - a property the tests
assert directly.

The result feeds two consumers:
  * Model 4 ranker examples (flagged ``oof=True``);
  * validation-fold evaluation of Model 1-3 candidates against their baselines
    and the deterministic Catalyst IQ scorer, before the final holdout is ever
    touched.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from catalystiq.ml.dataset.builder import TrainingDataset
from catalystiq.ml.models.heads import train_binary_head, train_quantile_head
from catalystiq.ml.models.training import (
    chronological_split,
    matrices_for,
    stable_feature_names,
)


@dataclass
class OOFPrediction:
    """One example's out-of-fold Model 1-3 outputs and realized outcome."""

    index: int
    symbol: str
    prediction_timestamp: str
    fold_id: int
    # Model 1
    net_profit_prob: float | None = None
    target_before_stop_prob: float | None = None
    # Model 2
    median_net_return: float | None = None
    # Model 3
    stop_breach_prob: float | None = None
    severe_adverse_excursion: float | None = None
    # realized outcome (for evaluation and the ranker target)
    net_profit_label: int | None = None
    net_terminal_return: float | None = None
    max_adverse_excursion: float | None = None
    stop_breach_label: int | None = None
    gap_beyond_stop_label: int | None = None
    round_trip_cost: float | None = None
    rule_based_setup_strength: float | None = None
    liquidity_cost_penalty: float | None = None
    market_sector_alignment: float | None = None
    sector: str | None = None


@dataclass
class OOFResult:
    predictions: dict[int, OOFPrediction]
    validation_indices: list[int]
    holdout_indices: list[int]
    n_folds: int
    fold_coverage: list[int] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def coverage_rate(self) -> float:
        develop = len(self.validation_indices)
        return (len(self.predictions) / develop) if develop else 0.0


def generate_oof_predictions(
    dataset: TrainingDataset,
    *,
    horizon_days: int,
    direction: str = "long",
    sector_of=None,
) -> OOFResult:
    """Generate OOF Model 1-3 predictions over the walk-forward folds.

    ``sector_of`` optionally maps a symbol to a sector string, used only to tag
    predictions for by-sector reporting (never as a model input).
    """
    feature_names = stable_feature_names(dataset)
    split = chronological_split(dataset)
    holdout = list(split.holdout_idx)
    validation_all: list[int] = []
    preds: dict[int, OOFPrediction] = {}
    fold_coverage: list[int] = []
    warnings: list[str] = []

    for fold in split.folds:
        val_idx = list(fold.validation)
        validation_all.extend(val_idx)
        # Seed a prediction record for every validation example (from realized
        # labels + features), then fill model outputs where a head can train.
        for i in val_idx:
            preds.setdefault(i, _seed_prediction(dataset, i, fold.fold_id, sector_of))

        _binary_oof(dataset, feature_names, fold, "net_profit",
                    lambda ex: ex.labels.net_profit_label, preds, warnings)
        _binary_oof(dataset, feature_names, fold, "target_before_stop",
                    lambda ex: ex.labels.target_before_stop_label, preds, warnings)
        _binary_oof(dataset, feature_names, fold, "stop_breach",
                    lambda ex: ex.labels.stop_breach_label, preds, warnings)
        _quantile_oof(dataset, feature_names, fold, "median_net_return",
                      lambda ex: ex.labels.net_terminal_return, "q50", preds, warnings)
        _quantile_oof(dataset, feature_names, fold, "severe_adverse_excursion",
                      lambda ex: ex.labels.max_adverse_excursion, "q10", preds, warnings)
        fold_coverage.append(len(val_idx))

    return OOFResult(
        predictions=preds,
        validation_indices=sorted(set(validation_all)),
        holdout_indices=holdout,
        n_folds=len(split.folds),
        fold_coverage=fold_coverage,
        warnings=warnings,
    )


def _seed_prediction(dataset, i: int, fold_id: int, sector_of) -> OOFPrediction:
    ex = dataset.examples[i]
    feats = ex.features
    return OOFPrediction(
        index=i,
        symbol=ex.symbol,
        prediction_timestamp=ex.prediction_timestamp.isoformat(),
        fold_id=fold_id,
        net_profit_label=ex.labels.net_profit_label,
        net_terminal_return=ex.labels.net_terminal_return,
        max_adverse_excursion=ex.labels.max_adverse_excursion,
        stop_breach_label=ex.labels.stop_breach_label,
        gap_beyond_stop_label=ex.labels.gap_beyond_stop_label,
        round_trip_cost=ex.labels.round_trip_cost,
        rule_based_setup_strength=_num(feats.get("rule_based_setup_strength")),
        liquidity_cost_penalty=_liquidity_penalty(feats),
        market_sector_alignment=_alignment(feats),
        sector=sector_of(ex.symbol) if sector_of else None,
    )


def _binary_oof(dataset, feature_names, fold, name, getter, preds, warnings) -> None:
    X_tr, y_tr, _ = matrices_for(dataset, feature_names, fold.train, getter)
    X_cal, y_cal, _ = matrices_for(dataset, feature_names, fold.calibration, getter)
    X_val, y_val, val_kept = matrices_for(dataset, feature_names, fold.validation, getter)
    if len(np.unique(y_tr)) < 2 or len(np.unique(y_cal)) < 2 or len(val_kept) == 0:
        warnings.append(f"fold {fold.fold_id} {name}: insufficient class variety for OOF head")
        return
    try:
        result = train_binary_head(
            X_train=X_tr, y_train=y_tr, X_calib=X_cal, y_calib=y_cal,
            X_eval=X_val, y_eval=y_val, feature_names=feature_names,
        )
        probs = result.head.predict_proba(X_val)
    except Exception as exc:  # pragma: no cover - defensive
        warnings.append(f"fold {fold.fold_id} {name}: OOF head failed ({exc})")
        return
    attr = {"net_profit": "net_profit_prob", "target_before_stop": "target_before_stop_prob",
            "stop_breach": "stop_breach_prob"}[name]
    for j, idx in enumerate(val_kept):
        setattr(preds[idx], attr, float(probs[j]))


def _quantile_oof(dataset, feature_names, fold, name, getter, level, preds, warnings) -> None:
    X_tr, y_tr, _ = matrices_for(dataset, feature_names, fold.train, getter)
    X_val, y_val, val_kept = matrices_for(dataset, feature_names, fold.validation, getter)
    if len(y_tr) < 30 or len(val_kept) == 0:
        warnings.append(f"fold {fold.fold_id} {name}: too few examples for OOF quantile head")
        return
    try:
        result = train_quantile_head(
            X_train=X_tr, y_train=y_tr, X_eval=X_val, y_eval=y_val, feature_names=feature_names,
        )
        out = result.head.predict(X_val)
    except Exception as exc:  # pragma: no cover - defensive
        warnings.append(f"fold {fold.fold_id} {name}: OOF quantile head failed ({exc})")
        return
    if level not in out:
        return
    vals = out[level]
    attr = {"median_net_return": "median_net_return",
            "severe_adverse_excursion": "severe_adverse_excursion"}[name]
    for j, idx in enumerate(val_kept):
        setattr(preds[idx], attr, float(vals[j]))


def _num(v):
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def _liquidity_penalty(feats: dict) -> float | None:
    """A 0..1 liquidity/cost penalty proxy from the estimated spread (higher =
    costlier). Derived from an existing point-in-time feature, not fabricated."""
    bps = _num(feats.get("estimated_spread_bps"))
    if bps is None:
        return None
    # 0 bps -> 0 penalty, 50+ bps -> capped at 1.
    return float(max(0.0, min(1.0, bps / 50.0)))


def _alignment(feats: dict) -> float | None:
    """A 0..1 market/sector-alignment proxy from 20-day market & sector returns
    (both positive -> aligned). Uses existing features only."""
    mkt = _num(feats.get("market_return_20d"))
    sec = _num(feats.get("sector_return_20d"))
    vals = [v for v in (mkt, sec) if v is not None]
    if not vals:
        return None
    positive = sum(1 for v in vals if v > 0)
    return float(positive / len(vals))
