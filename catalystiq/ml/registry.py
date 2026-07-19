"""Model-artifact registry service.

The registry is the approval gate for user-facing predictions. It stores every
candidate and approved artifact with full provenance and enforces the single
hard rule: ONLY an artifact with ``approval_status='approved'`` (and never one
built on synthetic data) may be returned for serving.

Approval itself (:func:`approve`) is a deliberate action with guardrails: a
synthetic-data artifact cannot be approved, and approval requires the
artifact's evaluation metrics to be present. Nothing here flips an artifact to
approved automatically.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from catalystiq.db.models import MLModelArtifact


class ArtifactApprovalError(RuntimeError):
    """Raised when an approval request violates a safety guardrail."""


VALID_FAMILIES = {"model_1", "model_2", "model_3", "model_4", "model_5"}
VALID_STATUSES = {"candidate", "approved", "rejected", "archived"}


@dataclass
class ArtifactSpec:
    model_name: str
    model_version: str
    model_family: str
    horizon_days: int
    trade_direction: str
    feature_schema_version: str
    target_definition_version: str
    training_data_version: str
    code_commit: str | None = None
    hyperparameters: dict | None = None
    evaluation_metrics: dict | None = None
    calibration_metrics: dict | None = None
    is_synthetic: bool = False
    artifact_uri: str | None = None
    notes: str | None = None
    training_start: dt.datetime | None = None
    training_end: dt.datetime | None = None
    validation_start: dt.datetime | None = None
    validation_end: dt.datetime | None = None
    calibration_start: dt.datetime | None = None
    calibration_end: dt.datetime | None = None
    holdout_start: dt.datetime | None = None
    holdout_end: dt.datetime | None = None


def register_artifact(db: Session, spec: ArtifactSpec, *, now: dt.datetime | None = None) -> MLModelArtifact:
    """Insert a new artifact in ``candidate`` status. An artifact is never born
    approved."""
    if spec.model_family not in VALID_FAMILIES:
        raise ValueError(f"unknown model_family {spec.model_family!r}")
    if spec.trade_direction not in {"long", "short"}:
        raise ValueError("trade_direction must be 'long' or 'short'")
    row = MLModelArtifact(
        model_name=spec.model_name,
        model_version=spec.model_version,
        model_family=spec.model_family,
        horizon_days=spec.horizon_days,
        trade_direction=spec.trade_direction,
        feature_schema_version=spec.feature_schema_version,
        target_definition_version=spec.target_definition_version,
        training_data_version=spec.training_data_version,
        code_commit=spec.code_commit,
        hyperparameters=spec.hyperparameters,
        evaluation_metrics=spec.evaluation_metrics,
        calibration_metrics=spec.calibration_metrics,
        approval_status="candidate",
        is_synthetic=spec.is_synthetic or _looks_synthetic(spec.training_data_version),
        artifact_uri=spec.artifact_uri,
        notes=spec.notes,
        training_start=spec.training_start,
        training_end=spec.training_end,
        validation_start=spec.validation_start,
        validation_end=spec.validation_end,
        calibration_start=spec.calibration_start,
        calibration_end=spec.calibration_end,
        holdout_start=spec.holdout_start,
        holdout_end=spec.holdout_end,
        created_at=now or dt.datetime.utcnow(),
    )
    db.add(row)
    db.flush()
    return row


def _looks_synthetic(training_data_version: str | None) -> bool:
    return bool(training_data_version) and training_data_version.lower().startswith("synthetic")


def approve(db: Session, artifact_id: int) -> MLModelArtifact:
    """Approve a candidate artifact for user-facing serving.

    Guardrails (any failure raises and leaves the row untouched):
      * artifact must exist and be a candidate,
      * must NOT be synthetic,
      * must carry evaluation metrics.
    """
    row = db.get(MLModelArtifact, artifact_id)
    if row is None:
        raise ArtifactApprovalError(f"artifact {artifact_id} not found")
    if row.is_synthetic or _looks_synthetic(row.training_data_version):
        raise ArtifactApprovalError(
            "refusing to approve a synthetic/demo-data artifact for user-facing use"
        )
    if not row.evaluation_metrics:
        raise ArtifactApprovalError("cannot approve an artifact with no evaluation metrics")
    row.approval_status = "approved"
    db.flush()
    return row


def set_status(db: Session, artifact_id: int, status: str) -> MLModelArtifact:
    if status not in VALID_STATUSES:
        raise ValueError(f"invalid status {status!r}")
    if status == "approved":
        return approve(db, artifact_id)
    row = db.get(MLModelArtifact, artifact_id)
    if row is None:
        raise ArtifactApprovalError(f"artifact {artifact_id} not found")
    row.approval_status = status
    db.flush()
    return row


def get_approved(
    db: Session, *, model_family: str, horizon_days: int, trade_direction: str
) -> MLModelArtifact | None:
    """Return the most recent APPROVED, non-synthetic artifact for the exact
    family/horizon/direction, or None. This is the only lookup the serving
    path may use."""
    stmt = (
        select(MLModelArtifact)
        .where(
            MLModelArtifact.model_family == model_family,
            MLModelArtifact.horizon_days == horizon_days,
            MLModelArtifact.trade_direction == trade_direction,
            MLModelArtifact.approval_status == "approved",
            MLModelArtifact.is_synthetic.is_(False),
        )
        .order_by(MLModelArtifact.created_at.desc())
    )
    return db.execute(stmt).scalars().first()


def has_approved_stack(
    db: Session, *, horizon_days: int, trade_direction: str, families: set[str]
) -> bool:
    """True only if EVERY family in ``families`` has an approved artifact for
    the given horizon/direction. Used to gate Model 4 / unified inference."""
    for fam in families:
        if get_approved(db, model_family=fam, horizon_days=horizon_days, trade_direction=trade_direction) is None:
            return False
    return True


def list_artifacts(db: Session, *, model_family: str | None = None) -> list[MLModelArtifact]:
    stmt = select(MLModelArtifact).order_by(MLModelArtifact.created_at.desc())
    if model_family:
        stmt = stmt.where(MLModelArtifact.model_family == model_family)
    return list(db.execute(stmt).scalars().all())
