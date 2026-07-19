"""Historical training-example builder.

Turns a set of (symbol, prediction_timestamp, direction) requests into fully
labeled, look-ahead-free training examples by consuming the provider-neutral
:class:`~catalystiq.ml.features.provider.PointInTimeFeatureProvider`. It never
calls an external provider directly.

Each example carries:
  * the point-in-time feature vector (validated by the feature schema),
  * the outcome labels (from the executable next-session entry + forward path),
  * complete provenance (schema/target/cost versions, both-touched policy),
  * a ``requirement_gaps`` record when a needed feature had no wired source.

The resulting :class:`TrainingDataset` is stamped with a
``training_data_version`` and an ``is_synthetic`` flag. Inference refuses to
serve any artifact whose dataset was synthetic - synthetic data may back unit
tests only, never a user-facing model.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from dataclasses import dataclass, field
from typing import Callable, Iterable, Sequence

from catalystiq.ml import (
    FEATURE_SCHEMA_VERSION,
    TARGET_DEFINITION_VERSION,
)
from catalystiq.ml.features.provider import PointInTimeFeatureProvider
from catalystiq.ml.features.schema import (
    DataQualityStatus,
    FeatureRejection,
    build_feature_vector,
)
from catalystiq.ml.labels.barriers import Bar, BothTouchedPolicy
from catalystiq.ml.labels.costs import CostModel, DEFAULT_COST_MODEL
from catalystiq.ml.labels.outcomes import OutcomeLabels, generate_outcome_labels


@dataclass(frozen=True)
class ExampleRequest:
    symbol: str
    prediction_timestamp: dt.datetime
    direction: str = "long"
    horizon_days: int = 5


# A planner decides target/stop using ONLY the point-in-time feature vector.
# It must not consult the forward path. Default is ATR-multiple barriers.
BarrierPlanner = Callable[[float, str, dict], "BarrierPlan"]


@dataclass(frozen=True)
class BarrierPlan:
    target_price: float
    stop_price: float


def atr_barrier_planner(
    entry_price: float, direction: str, features: dict, *, target_atr: float = 2.0, stop_atr: float = 1.0
) -> BarrierPlan:
    """Symmetric-ish ATR barriers computed from point-in-time features only.

    Falls back to a fixed percentage if ATR is unavailable, so a plan always
    exists; the caller can reject examples lacking ATR upstream if desired.
    """
    atr = features.get("atr_14")
    if atr is None or atr <= 0:
        band = 0.03 * entry_price
    else:
        band = atr
    if direction == "long":
        return BarrierPlan(entry_price + target_atr * band, entry_price - stop_atr * band)
    return BarrierPlan(entry_price - target_atr * band, entry_price + stop_atr * band)


@dataclass(frozen=True)
class TrainingExample:
    symbol: str
    prediction_timestamp: dt.datetime
    entry_session: dt.datetime
    direction: str
    horizon_days: int
    features: dict[str, float | int | None]
    labels: OutcomeLabels
    feature_rejections: list[FeatureRejection] = field(default_factory=list)
    requirement_gaps: list[str] = field(default_factory=list)


@dataclass
class TrainingDataset:
    examples: list[TrainingExample] = field(default_factory=list)
    feature_schema_version: str = FEATURE_SCHEMA_VERSION
    target_definition_version: str = TARGET_DEFINITION_VERSION
    cost_model_version: str = DEFAULT_COST_MODEL.version
    both_touched_policy: str = BothTouchedPolicy.STOP_FIRST.value
    is_synthetic: bool = False
    source_providers: list[str] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)

    @property
    def size(self) -> int:
        return len(self.examples)

    @property
    def date_coverage(self) -> tuple[dt.datetime | None, dt.datetime | None]:
        if not self.examples:
            return (None, None)
        ts = [e.prediction_timestamp for e in self.examples]
        return (min(ts), max(ts))

    def training_data_version(self) -> str:
        """Stable content hash used as ``training_data_version`` in the
        registry. Changes iff the dataset's identity-defining metadata or its
        example keys change."""
        start, end = self.date_coverage
        payload = {
            "feature_schema_version": self.feature_schema_version,
            "target_definition_version": self.target_definition_version,
            "cost_model_version": self.cost_model_version,
            "both_touched_policy": self.both_touched_policy,
            "is_synthetic": self.is_synthetic,
            "size": self.size,
            "coverage": [start.isoformat() if start else None, end.isoformat() if end else None],
            "keys": sorted(
                f"{e.symbol}|{e.prediction_timestamp.isoformat()}|{e.direction}|{e.horizon_days}"
                for e in self.examples
            )[:10_000],
        }
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]
        prefix = "synthetic" if self.is_synthetic else "data"
        return f"{prefix}-{digest}"


class TrainingExampleBuilder:
    def __init__(
        self,
        provider: PointInTimeFeatureProvider,
        *,
        cost_model: CostModel = DEFAULT_COST_MODEL,
        both_touched_policy: BothTouchedPolicy = BothTouchedPolicy.STOP_FIRST,
        barrier_planner: BarrierPlanner = atr_barrier_planner,
        for_training: bool = True,
        is_synthetic: bool = False,
        source_providers: Sequence[str] | None = None,
    ) -> None:
        self.provider = provider
        self.cost_model = cost_model
        self.both_touched_policy = both_touched_policy
        self.barrier_planner = barrier_planner
        self.for_training = for_training
        self.is_synthetic = is_synthetic
        self.source_providers = list(source_providers or [])

    def build(self, requests: Iterable[ExampleRequest]) -> TrainingDataset:
        dataset = TrainingDataset(
            both_touched_policy=self.both_touched_policy.value,
            cost_model_version=self.cost_model.version,
            is_synthetic=self.is_synthetic,
            source_providers=self.source_providers,
        )
        for req in requests:
            example = self._build_one(req, dataset)
            if example is not None:
                dataset.examples.append(example)
        return dataset

    def _build_one(self, req: ExampleRequest, dataset: TrainingDataset) -> TrainingExample | None:
        raw_features = self.provider.get_features(req.symbol, req.prediction_timestamp)
        vector, rejections = build_feature_vector(
            raw_features, for_training=self.for_training, strict=True
        )
        requirement_gaps = [
            f.feature_name
            for f in raw_features
            if f.data_quality_status is DataQualityStatus.MISSING
        ]

        entry = self.provider.get_executable_entry(req.symbol, req.prediction_timestamp)
        if entry is None:
            dataset.skipped.append(
                {"symbol": req.symbol, "ts": req.prediction_timestamp.isoformat(),
                 "reason": "no executable entry available"}
            )
            return None
        entry_session, entry_price = entry

        path: list[Bar] = self.provider.get_forward_path(
            req.symbol, entry_session, req.horizon_days
        )
        if not path:
            dataset.skipped.append(
                {"symbol": req.symbol, "ts": req.prediction_timestamp.isoformat(),
                 "reason": "no forward path available"}
            )
            return None

        plan = self.barrier_planner(entry_price, req.direction, vector)
        labels = generate_outcome_labels(
            symbol=req.symbol,
            direction=req.direction,
            horizon_days=req.horizon_days,
            executable_entry_price=entry_price,
            target_price=plan.target_price,
            stop_price=plan.stop_price,
            path=path,
            estimated_spread_fraction=_spread_fraction(vector),
            avg_daily_dollar_volume=vector.get("adv_dollar_20d"),
            cost_model=self.cost_model,
            both_touched_policy=self.both_touched_policy,
        )

        return TrainingExample(
            symbol=req.symbol,
            prediction_timestamp=req.prediction_timestamp,
            entry_session=entry_session,
            direction=req.direction,
            horizon_days=req.horizon_days,
            features=vector,
            labels=labels,
            feature_rejections=rejections,
            requirement_gaps=requirement_gaps,
        )


def _spread_fraction(vector: dict) -> float | None:
    bps = vector.get("estimated_spread_bps")
    if bps is None:
        return None
    try:
        return float(bps) / 10_000.0
    except (TypeError, ValueError):
        return None
