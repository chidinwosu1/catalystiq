"""ML inference endpoints - DISABLED by default, fail closed.

Every route here refuses to serve a prediction unless the ML subsystem is
enabled AND approved, non-synthetic model artifacts exist. In the current
build that means they return the stable ``not_available`` contract shape,
never placeholder probabilities or demo values. The routes exist now so the
frontend can integrate against a stable surface; turning models on later
requires no shape change.

``/ml/status`` and ``/ml/feature-requirements`` expose non-sensitive
subsystem/manifest metadata (flag states, which features still need a wired
source) and are safe to read while everything is disabled.
"""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from catalystiq.auth import verify_action_key
from catalystiq.config import get_settings
from catalystiq.db.base import get_db
from catalystiq.ml import flags
from catalystiq.ml.features.manifest import manifest_dict
from catalystiq.ml.inference import assemble_unified_inference, ml_status
from catalystiq.ml.registry import list_artifacts
from catalystiq.ml.schemas import (
    MLStatus,
    NotAvailable,
    RankingUnavailable,
)

router = APIRouter(
    prefix="/ml",
    tags=["ml"],
    dependencies=[Depends(verify_action_key)],
)


@router.get("/status", response_model=MLStatus)
def get_ml_status(db: Session = Depends(get_db)):
    """Report what the ML subsystem is permitted to do (all off by default)."""
    return ml_status(db)


@router.get("/feature-requirements")
def get_feature_requirements():
    """Machine-readable manifest of required point-in-time features and
    whether a provider-neutral source is wired. Missing features are recorded
    here, never fabricated."""
    return manifest_dict()


@router.get("/inference/{symbol}", response_model=None)
def get_unified_inference(
    symbol: str,
    direction: str = Query(default="long"),
    horizon_days: int = Query(default=5),
    db: Session = Depends(get_db),
) -> object:
    """Unified five-model inference contract. Returns ``not_available`` until
    validated, approved artifacts exist and inference is enabled."""
    result = assemble_unified_inference(
        db,
        symbol=symbol.upper(),
        prediction_timestamp=dt.datetime.utcnow(),
        direction=direction,
        horizon_days=horizon_days,
    )
    return result


@router.get("/ranking", response_model=None)
def get_opportunity_ranking(
    direction: str = Query(default="long"),
    horizon_days: int = Query(default=5),
    db: Session = Depends(get_db),
) -> object:
    """Model 4 cross-sectional opportunity ranking. Disabled until the ranker
    and Models 1-3 have approved artifacts; never returns demo opportunities."""
    gate = flags.ranking_enabled(get_settings())
    if not gate.allowed:
        return RankingUnavailable(reason=gate.reason)
    # Even when enabled, an approved ranker + M1-3 stack is required; the
    # serving loader is not wired in this phase, so fail closed.
    return RankingUnavailable(
        reason="No sufficiently reliable opportunities identified"
    )


@router.get("/behavior/{symbol}", response_model=None)
def get_behavior_analysis(symbol: str, db: Session = Depends(get_db)) -> object:
    """Model 5 aggregate investor functional response. Disabled until a
    validated event/response model is approved; returns the unavailable state
    rather than combining real prices with fictional antecedents."""
    gate = flags.behavior_inference_enabled(get_settings())
    if not gate.allowed:
        return NotAvailable(
            reason=(
                "Aggregate behavior analysis unavailable - validated event and "
                "response models have not yet been approved."
            )
        )
    return NotAvailable(
        reason="Approved behavior artifacts are registered but the serving loader is not enabled"
    )


@router.get("/registry")
def get_registry(
    model_family: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    """List registered model artifacts (metadata only)."""
    rows = list_artifacts(db, model_family=model_family)
    return {
        "count": len(rows),
        "artifacts": [
            {
                "id": r.id,
                "model_name": r.model_name,
                "model_version": r.model_version,
                "model_family": r.model_family,
                "horizon_days": r.horizon_days,
                "trade_direction": r.trade_direction,
                "approval_status": r.approval_status,
                "is_synthetic": r.is_synthetic,
                "feature_schema_version": r.feature_schema_version,
                "target_definition_version": r.target_definition_version,
                "training_data_version": r.training_data_version,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ],
    }
